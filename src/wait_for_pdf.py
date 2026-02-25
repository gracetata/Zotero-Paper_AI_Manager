"""
wait_for_pdf.py — 轮询等待论文 PDF 下载完成

用法:
  python wait_for_pdf.py ITEM_KEY [--timeout 300] [--interval 15]

退出码:
  0 = PDF 已找到
  1 = 超时（PDF 未下载）
  2 = 错误
"""

import os
import sys
import time
import argparse
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from zotero_client import ZoteroClient

PENDING_FILE = os.path.join(os.path.dirname(__file__), '..', '.pending_pdf')


def pdf_exists(zotero_client, item_key):
    """检查指定条目是否有本地 PDF"""
    pdf = zotero_client.find_local_pdf(item_key)
    if pdf:
        return pdf
    pdf = zotero_client.find_pdf_via_attachments(item_key)
    return pdf


def add_to_pending(item_key):
    """将条目加入 .pending_pdf 等待队列"""
    pending = load_pending()
    pending.add(item_key)
    with open(PENDING_FILE, 'w') as f:
        for k in sorted(pending):
            f.write(k + '\n')


def remove_from_pending(item_key):
    """从 .pending_pdf 移除条目"""
    pending = load_pending()
    pending.discard(item_key)
    with open(PENDING_FILE, 'w') as f:
        for k in sorted(pending):
            f.write(k + '\n')


def load_pending():
    """读取 .pending_pdf 文件中的条目 key 集合"""
    if not os.path.exists(PENDING_FILE):
        return set()
    with open(PENDING_FILE, 'r') as f:
        return set(line.strip() for line in f if line.strip())


def main():
    parser = argparse.ArgumentParser(description='等待 Zotero 论文 PDF 下载')
    parser.add_argument('item_key', help='Zotero item key')
    parser.add_argument('--timeout', type=int, default=300,
                        help='最长等待秒数（默认 300 秒 / 5 分钟）')
    parser.add_argument('--interval', type=int, default=15,
                        help='轮询间隔秒数（默认 15 秒）')
    args = parser.parse_args()

    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)

    try:
        zc = ZoteroClient(config)
    except Exception as e:
        print(f"❌ 初始化 Zotero 客户端失败: {e}", file=sys.stderr)
        sys.exit(2)

    # 先检查一次
    pdf = pdf_exists(zc, args.item_key)
    if pdf:
        print(f"✅ PDF 已就绪: {os.path.basename(pdf)}")
        remove_from_pending(args.item_key)
        sys.exit(0)

    print(f"⏳ PDF 尚未下载，最长等待 {args.timeout} 秒...")
    print(f"   （每 {args.interval}s 检查一次，可按 Ctrl+C 跳过等待）")

    elapsed = 0
    try:
        while elapsed < args.timeout:
            time.sleep(args.interval)
            elapsed += args.interval
            remaining = args.timeout - elapsed
            pdf = pdf_exists(zc, args.item_key)
            if pdf:
                print(f"\n✅ PDF 已下载！({elapsed}s 后) {os.path.basename(pdf)}")
                remove_from_pending(args.item_key)
                sys.exit(0)
            bars = '█' * (elapsed // args.interval) + '░' * ((args.timeout - elapsed) // args.interval)
            print(f"   [{elapsed:3d}s] 等待中... [{bars}] 剩余 {remaining}s", end='\r')
    except KeyboardInterrupt:
        print(f"\n⏭️  用户跳过等待")
        add_to_pending(args.item_key)
        sys.exit(1)

    # 超时
    print(f"\n⏰ 等待超时（{args.timeout}s），PDF 尚未下载")
    print(f"   ✍️  已记录此条目：当您为其添加 PDF 后，将自动触发分析")
    add_to_pending(args.item_key)
    sys.exit(1)


if __name__ == '__main__':
    main()
