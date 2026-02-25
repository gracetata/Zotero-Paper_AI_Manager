"""
pdf_to_text.py — 供 VS Code 扩展调用，提取 PDF 文本到 stdout

用法: python3 pdf_to_text.py ITEM_KEY

输出格式（stdout）:
  第1行: 状态信息（字符数、页数）
  第2行起: PDF 文本内容

退出码: 0=成功, 1=无PDF, 2=错误
"""

import os
import sys
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from zotero_client import ZoteroClient


def main():
    if len(sys.argv) < 2:
        print("用法: pdf_to_text.py ITEM_KEY", file=sys.stderr)
        sys.exit(2)

    item_key = sys.argv[1].strip().upper()
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)

    zc = ZoteroClient(config)

    # 查找本地 PDF
    pdf_path = zc.find_local_pdf(item_key) or zc.find_pdf_via_attachments(item_key)
    if not pdf_path:
        print(f"STATUS: 未找到 PDF，item_key={item_key}", flush=True)
        sys.exit(1)

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        pages = len(doc)
        text = ''
        for page in doc:
            text += page.get_text()
        doc.close()

        char_count = len(text)
        print(f"STATUS: 提取成功 | {pages} 页 | {char_count} 字符 | {os.path.basename(pdf_path)}")
        print(text, end='')
        sys.exit(0)
    except Exception as e:
        print(f"STATUS: 提取失败 - {e}", flush=True)
        sys.exit(2)


if __name__ == '__main__':
    main()
