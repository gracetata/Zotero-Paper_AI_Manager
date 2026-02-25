"""
watch_zotero.py — 全自动模式：监控 Zotero 数据库变化，弹出终端分析新论文

架构：
  文件系统 watchdog → 检测到 DB 变化 → 设 dirty 标志
  后台轮询线程 → 每隔 N 秒（或 dirty 后延迟）调用 Zotero Web API 查询新条目
  → 发现新条目 → 弹出 gnome-terminal 运行 analyze_and_chat.sh

用法:
  python watch_zotero.py              # 持续监控
  python watch_zotero.py --once       # 检查一次新条目后退出
"""

import os
import sys
import time
import threading
import subprocess
import yaml
import argparse
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

sys.path.insert(0, os.path.dirname(__file__))
from zotero_client import ZoteroClient


# ── 配置 ─────────────────────────────────────────────────────

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


# ── 已处理 ID 记录 ────────────────────────────────────────────

def load_processed_ids(processed_file):
    if os.path.exists(processed_file):
        with open(processed_file, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_processed_id(processed_file, item_key):
    with open(processed_file, 'a') as f:
        f.write(item_key + '\n')


# ── Zotero Web API 查询新条目（不读 SQLite，绕开锁）────────────

def get_new_items_via_api(zotero_client, processed_ids, limit=20):
    """
    调用 Zotero Web API 获取最近条目，过滤掉已处理的。
    返回新条目的 key 列表（最新的在前）。
    """
    try:
        recent = zotero_client.get_recent_items(limit=limit)
        return [
            item['data']['key']
            for item in recent
            if item['data']['key'] not in processed_ids
        ]
    except Exception as e:
        print(f"  [WARN] Zotero API 查询失败: {e}")
        return []


# ── 终端弹出 ──────────────────────────────────────────────────

def find_terminal():
    import shutil
    candidates = [
        ('gnome-terminal', ['gnome-terminal', '--title={title}', '--', 'bash', '-c', '{cmd}']),
        ('xterm',          ['xterm', '-title', '{title}', '-e', 'bash', '-c', '{cmd}']),
        ('konsole',        ['konsole', '--title', '{title}', '-e', 'bash -c {cmd_q}']),
    ]
    for exe, template in candidates:
        if shutil.which(exe):
            return exe, template
    return None, None


def popup_terminal_for_item(item_key, config):
    """在新终端窗口中分析指定论文，完成后提示追问"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    shell_script = os.path.join(script_dir, 'analyze_and_chat.sh')

    exe, template = find_terminal()
    title = f"📄 论文分析 — {item_key}"
    inner_cmd = f'bash "{shell_script}" "{item_key}"; exec bash'

    # 构建完整的环境变量（gnome-terminal 需要 DISPLAY + DBUS）
    env = os.environ.copy()
    if 'DISPLAY' not in env or not env['DISPLAY']:
        env['DISPLAY'] = ':1'
    if 'DBUS_SESSION_BUS_ADDRESS' not in env or not env['DBUS_SESSION_BUS_ADDRESS']:
        env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path=/run/user/{os.getuid()}/bus'

    if exe:
        cmd = []
        for part in template:
            cmd.append(
                part.replace('{title}', title)
                    .replace('{cmd}', inner_cmd)
                    .replace('{cmd_q}', f'"{inner_cmd}"')
            )
        try:
            subprocess.Popen(cmd, env=env, start_new_session=True)
            print(f"  🖥️  已弹出终端窗口（{exe}）")
            return True
        except Exception as e:
            print(f"  ⚠️  弹出终端失败: {e}，改用后台分析")
    else:
        print(f"  ⚠️  未找到图形终端，改用后台分析")

    # 后台兜底
    analyzer = os.path.join(script_dir, 'paper_analyzer.py')
    config_path = os.path.join(script_dir, '..', 'config.yaml')
    subprocess.Popen([sys.executable, analyzer, '--key', item_key, '--config', config_path])
    return False


# ── 文件系统事件处理（仅作触发信号）────────────────────────────

class ZoteroDBTrigger(FileSystemEventHandler):
    """监控 Zotero DB 文件变化，向主循环发送 dirty 信号"""

    def __init__(self, db_path, on_change_callback):
        self.db_path = db_path
        self._callback = on_change_callback
        self._last_signal = 0

    def on_modified(self, event):
        if event.is_directory:
            return
        # 只关注 zotero.sqlite 或 .sqlite-wal 变化
        src = event.src_path
        if not (self.db_path in src or src.endswith('.sqlite-wal')):
            return
        now = time.time()
        if now - self._last_signal < 5:   # 5s 内只发一次信号
            return
        self._last_signal = now
        self._callback()


# ── 主监控器 ─────────────────────────────────────────────────

class ZoteroWatcher:
    def __init__(self, config):
        self.config = config
        wdog_cfg = config.get('watchdog', {})
        self.db_path = wdog_cfg.get('zotero_db', os.path.expanduser('~/Zotero/zotero.sqlite'))
        self.processed_file = wdog_cfg.get(
            'processed_ids_file',
            os.path.join(os.path.dirname(__file__), '..', '.processed_ids')
        )
        # 等待 Zotero 写完整条目（含元数据+PDF）的时间
        self.wait_after_change = int(wdog_cfg.get('wait_after_change', 30))
        # 无文件变化时的兜底轮询间隔
        self.poll_interval = int(wdog_cfg.get('poll_interval_secs', 120))

        self._dirty = threading.Event()   # 文件系统变化标志
        self._stop = threading.Event()
        self._zotero_client = ZoteroClient(config)

    def _initialize_known_items(self):
        """
        启动时把当前 Zotero 库里所有条目标记为「已知」，
        这样 watchdog 只对启动之后新增的论文触发分析。
        """
        processed_ids = load_processed_ids(self.processed_file)
        try:
            all_items = self._zotero_client.get_all_items()
            all_keys = {it['data']['key'] for it in all_items}
            new_to_mark = all_keys - processed_ids
            if new_to_mark:
                with open(self.processed_file, 'a') as f:
                    for k in sorted(new_to_mark):
                        f.write(k + '\n')
                print(f"   ✅ 已将现有 {len(all_keys)} 篇论文标记为「已知」（新增 {len(new_to_mark)} 条）")
            else:
                print(f"   ✅ 已知条目记录完整（{len(all_keys)} 篇）")
        except Exception as e:
            print(f"   ⚠️  初始化已知条目失败: {e}（watchdog 仍会运行，但可能误报）")

    def _on_db_change(self):
        ts = datetime.now().strftime('%H:%M:%S')
        print(f"\n📡 [{ts}] 检测到 Zotero 数据库变化，{self.wait_after_change}s 后检查新条目...")
        self._dirty.set()

    def _check_and_process(self):
        """调用 Zotero API 查新条目并处理，每次最多处理1篇（防止级联弹窗）"""
        processed_ids = load_processed_ids(self.processed_file)
        new_keys = get_new_items_via_api(self._zotero_client, processed_ids, limit=20)

        if not new_keys:
            print(f"  ℹ️  暂无新增论文条目")
            return

        # 每次只处理1篇，避免同时弹出多个终端
        key = new_keys[0]
        if len(new_keys) > 1:
            print(f"  ℹ️  发现 {len(new_keys)} 篇新条目，本次处理第1篇，其余下次检查时处理")
        save_processed_id(self.processed_file, key)
        print(f"\n🚀 [{datetime.now().strftime('%H:%M:%S')}] 新论文: {key}")
        popup_terminal_for_item(key, self.config)

    def run(self):
        if not os.path.exists(self.db_path):
            print(f"❌ 找不到 Zotero 数据库: {self.db_path}")
            sys.exit(1)

        # 启动时先把现有所有条目标记为「已知」
        print(f"👁️  Zotero-Paper_AI_Manager 启动中...")
        print(f"   数据库: {self.db_path}")
        print(f"   检测到变化后等待 {self.wait_after_change}s 再查（让 Zotero 写完）")
        print(f"   兜底轮询间隔: {self.poll_interval}s")
        self._initialize_known_items()
        print(f"   ✅ 就绪，只对启动后新增的论文自动弹窗分析")
        print(f"   按 Ctrl+C 停止\n")

        # 启动文件系统监控
        trigger = ZoteroDBTrigger(self.db_path, self._on_db_change)
        observer = Observer()
        observer.schedule(trigger, path=os.path.dirname(self.db_path), recursive=False)
        observer.start()

        try:
            while not self._stop.is_set():
                # 等待 dirty 信号（文件变化）或超时（兜底轮询）
                changed = self._dirty.wait(timeout=self.poll_interval)
                if self._stop.is_set():
                    break
                if changed:
                    time.sleep(self.wait_after_change)
                    self._dirty.clear()
                print(f"\n🔍 [{datetime.now().strftime('%H:%M:%S')}] 检查新条目（via Zotero API）...")
                self._check_and_process()

        except KeyboardInterrupt:
            pass
        finally:
            observer.stop()
            observer.join()
            print("\n⏹️  监控已停止")


# ── 单次检查模式 ──────────────────────────────────────────────

def check_once(config):
    wdog_cfg = config.get('watchdog', {})
    processed_file = wdog_cfg.get(
        'processed_ids_file',
        os.path.join(os.path.dirname(__file__), '..', '.processed_ids')
    )
    zotero_client = ZoteroClient(config)
    processed_ids = load_processed_ids(processed_file)
    new_keys = get_new_items_via_api(zotero_client, processed_ids, limit=20)

    if not new_keys:
        print("✅ 没有发现未处理的新论文")
        return

    print(f"🔍 发现 {len(new_keys)} 篇未处理论文")
    for key in new_keys:
        save_processed_id(processed_file, key)
        popup_terminal_for_item(key, config)
        time.sleep(2)


# ── 入口 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Zotero 论文自动监控工具')
    parser.add_argument('--once', action='store_true', help='单次检查新条目后退出')
    args = parser.parse_args()

    config = load_config()
    if config['zotero']['api_key'] == 'YOUR_ZOTERO_API_KEY':
        print("❌ 请先在 config.yaml 中配置 Zotero API Key")
        sys.exit(1)

    if args.once:
        check_once(config)
        return

    ZoteroWatcher(config).run()


if __name__ == '__main__':
    main()
