"""
watch_zotero.py — 全自动模式：监控 Zotero 数据库变化，自动触发论文分析
使用 watchdog 库监控 zotero.sqlite 文件修改事件

用法:
  python watch_zotero.py              # 前台运行（Ctrl+C 停止）
  python watch_zotero.py --once       # 检查一次新条目后退出
"""

import os
import sys
import time
import sqlite3
import yaml
import argparse
import subprocess
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 添加 src 到路径
sys.path.insert(0, os.path.dirname(__file__))


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def load_processed_ids(processed_file):
    if os.path.exists(processed_file):
        with open(processed_file, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_processed_id(processed_file, item_key):
    with open(processed_file, 'a') as f:
        f.write(item_key + '\n')


def get_new_item_keys_from_db(db_path, processed_ids, limit=10):
    """
    直接查询 Zotero SQLite 数据库，获取最新添加的条目 keys。
    注意：Zotero 运行时数据库可能被锁定，使用 WAL mode 可以读取。
    """
    new_keys = []
    try:
        # 使用只读模式连接，避免干扰 Zotero 正常运行
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        cursor = conn.cursor()

        # 查询最近添加的顶级条目（非附件）
        cursor.execute("""
            SELECT i.key
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
            ORDER BY i.dateAdded DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        for (key,) in rows:
            if key not in processed_ids:
                new_keys.append(key)

    except sqlite3.OperationalError as e:
        # 数据库被锁定时跳过本次检查
        print(f"  [WARN] 数据库暂时不可读: {e}")

    return new_keys


def trigger_analysis(item_key, config_path):
    """调用 paper_analyzer.py 处理新条目"""
    analyzer_path = os.path.join(os.path.dirname(__file__), 'paper_analyzer.py')
    cmd = [sys.executable, analyzer_path, '--key', item_key, '--config', config_path]
    print(f"\n🆕 [{datetime.now().strftime('%H:%M:%S')}] 发现新论文 {item_key}，开始分析...")
    try:
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode != 0:
            print(f"  ❌ 分析进程退出码: {result.returncode}")
    except Exception as e:
        print(f"  ❌ 触发分析失败: {e}")


class ZoteroDBHandler(FileSystemEventHandler):
    """监控 Zotero 数据库目录的文件变化事件"""

    def __init__(self, db_path, processed_file, config_path, debounce_secs=5):
        self.db_path = db_path
        self.processed_file = processed_file
        self.config_path = config_path
        self.debounce_secs = debounce_secs
        self._last_trigger = 0

    def on_modified(self, event):
        # 只关注 zotero.sqlite 或其 WAL 文件的变化
        if not any(self.db_path in event.src_path for _ in [1]):
            return
        if event.is_directory:
            return

        now = time.time()
        if now - self._last_trigger < self.debounce_secs:
            return  # 防抖：避免短时间内重复触发
        self._last_trigger = now

        print(f"\n📡 [{datetime.now().strftime('%H:%M:%S')}] 检测到 Zotero 数据库变化，检查新条目...")

        processed_ids = load_processed_ids(self.processed_file)
        new_keys = get_new_item_keys_from_db(self.db_path, processed_ids, limit=5)

        if not new_keys:
            print("  ℹ️  无新增论文条目")
            return

        for key in new_keys:
            # 先标记为已处理，防止重复
            save_processed_id(self.processed_file, key)
            trigger_analysis(key, self.config_path)


def check_once(config):
    """单次检查模式：处理所有未分析的新条目"""
    wdog_cfg = config.get('watchdog', {})
    db_path = wdog_cfg.get('zotero_db', os.path.expanduser('~/Zotero/zotero.sqlite'))
    processed_file = wdog_cfg.get('processed_ids_file',
                                   os.path.join(os.path.dirname(__file__), '..', '.processed_ids'))
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')

    processed_ids = load_processed_ids(processed_file)
    new_keys = get_new_item_keys_from_db(db_path, processed_ids, limit=20)

    if not new_keys:
        print("✅ 没有发现未处理的新论文")
        return

    print(f"🔍 发现 {len(new_keys)} 篇未处理论文")
    for key in new_keys:
        save_processed_id(processed_file, key)
        trigger_analysis(key, config_path)


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

    # 持续监控模式
    wdog_cfg = config.get('watchdog', {})
    db_path = wdog_cfg.get('zotero_db', os.path.expanduser('~/Zotero/zotero.sqlite'))
    db_dir = os.path.dirname(db_path)
    processed_file = wdog_cfg.get('processed_ids_file',
                                   os.path.join(os.path.dirname(__file__), '..', '.processed_ids'))
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    debounce = wdog_cfg.get('debounce_seconds', 5)

    if not os.path.exists(db_path):
        print(f"❌ 找不到 Zotero 数据库: {db_path}")
        print("   请检查 config.yaml 中的 watchdog.zotero_db 路径")
        sys.exit(1)

    print(f"👁️  开始监控 Zotero 数据库...")
    print(f"   数据库路径: {db_path}")
    print(f"   防抖间隔: {debounce}s")
    print(f"   按 Ctrl+C 停止\n")

    event_handler = ZoteroDBHandler(db_path, processed_file, config_path, debounce_secs=debounce)
    observer = Observer()
    observer.schedule(event_handler, path=db_dir, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n\n⏹️  监控已停止")

    observer.join()


if __name__ == '__main__':
    main()
