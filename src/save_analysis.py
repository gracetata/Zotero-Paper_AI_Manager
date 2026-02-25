"""
save_analysis.py — 接收 VS Code 扩展通过 stdin 传入的分析文本，写回 Zotero

用法: python3 save_analysis.py ITEM_KEY < analysis.txt

完成的操作：
  - 提取标签（从分析末尾 TAGS: [...] 行）
  - 保存 Markdown 笔记到 notes/ 目录
  - 写入 Zotero 笔记（HTML note）
  - 添加 Zotero 标签
  - 更新 INDEX.md
  - 创建 Zotero 链接附件（指向 .md 文件）

退出码: 0=成功, 1=错误
"""

import os
import sys
import re
import yaml
import datetime

sys.path.insert(0, os.path.dirname(__file__))
from zotero_client import ZoteroClient
from github_models_client import extract_tags_from_analysis


def markdown_to_html(text: str) -> str:
    """极简 Markdown → HTML 转换（供 Zotero 笔记用）"""
    lines = text.split('\n')
    html_lines = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('# '):
            html_lines.append(f'<h1>{line[2:]}</h1>')
        elif line.startswith('## '):
            html_lines.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('### '):
            html_lines.append(f'<h3>{line[4:]}</h3>')
        elif line.startswith('- ') or line.startswith('* '):
            html_lines.append(f'<li>{line[2:]}</li>')
        elif line.startswith('**') and line.endswith('**'):
            html_lines.append(f'<p><strong>{line[2:-2]}</strong></p>')
        elif line == '':
            html_lines.append('<br>')
        else:
            html_lines.append(f'<p>{line}</p>')
    return '\n'.join(html_lines)


def strip_tags_line(text: str):
    """从分析文本中提取并移除 TAGS: [...] 行，返回 (clean_text, tags_line)"""
    pattern = r'\nTAGS:\s*\[.*?\]\s*$'
    m = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
    if m:
        tags_line = text[m.start():].strip()
        clean = text[:m.start()].rstrip()
        return clean, tags_line
    return text, ''


def update_index(index_path: str, title: str, item_key: str, tags: list, md_rel: str):
    """在 INDEX.md 末尾追加条目"""
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')
    tag_str = ', '.join(tags) if tags else '—'
    entry = f"\n| {date_str} | [{title}]({md_rel}) | {tag_str} | `{item_key}` |"
    if not os.path.exists(index_path):
        header = (
            "# 论文分析索引\n\n"
            "| 日期 | 标题 | 标签 | Key |\n"
            "|------|------|------|-----|\n"
        )
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(header + entry + '\n')
    else:
        with open(index_path, 'a', encoding='utf-8') as f:
            f.write(entry + '\n')


def main():
    if len(sys.argv) < 2:
        print("用法: save_analysis.py ITEM_KEY  (analysis from stdin)", file=sys.stderr)
        sys.exit(1)

    item_key = sys.argv[1].strip().upper()
    analysis = sys.stdin.read()

    if not analysis.strip():
        print("❌ stdin 为空，没有分析内容", file=sys.stderr)
        sys.exit(1)

    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)

    zc = ZoteroClient(config)

    # 获取论文元数据
    try:
        item = zc.zot.item(item_key)
        data = item['data']
        title = data.get('title', f'Paper_{item_key}')
        year = ''
        if data.get('date'):
            year = data['date'][:4]
        elif data.get('year'):
            year = str(data['year'])
    except Exception as e:
        print(f"⚠️  获取元数据失败: {e}，使用默认标题", file=sys.stderr)
        title = f'Paper_{item_key}'
        year = datetime.datetime.now().strftime('%Y')

    print(f"📝 条目: {title[:60]}")

    # 清理代码块
    clean_analysis = re.sub(r'^```[\w]*\n', '', analysis, flags=re.MULTILINE)
    clean_analysis = re.sub(r'\n```\s*$', '', clean_analysis, flags=re.MULTILINE)
    clean_analysis, _ = strip_tags_line(clean_analysis)

    # 提取标签（严格白名单）
    tags = extract_tags_from_analysis(analysis, config)
    print(f"🏷️  标签: {tags}")

    # 保存 Markdown
    notes_dir = config.get('output', {}).get('notes_dir',
                os.path.join(os.path.dirname(__file__), '..', 'notes'))
    year_dir = os.path.join(notes_dir, year or 'unknown')
    os.makedirs(year_dir, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:80]
    md_filename = f"{safe_title}.md"
    md_path = os.path.join(year_dir, md_filename)

    # 构建 Markdown 文件头
    read_note = f"> 📊 **Copilot (vscode.lm) 分析** | 模型: Claude via GitHub Copilot"
    md_content = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"zotero_key: {item_key}\n"
        f"tags: {tags}\n"
        f"date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"---\n\n"
        f"{read_note}\n\n"
        f"{clean_analysis}\n"
    )
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"💾 Markdown: {md_path}")

    # 更新 INDEX.md
    index_path = os.path.join(notes_dir, 'INDEX.md')
    md_rel = os.path.relpath(md_path, notes_dir)
    update_index(index_path, title[:60], item_key, tags, md_rel)
    print(f"📋 INDEX 已更新")

    # 写入 Zotero 笔记
    note_html = markdown_to_html(
        f"# {title}\n\n{read_note}\n\n{clean_analysis}"
    )
    try:
        zc.add_note(item_key, note_html)
        print(f"✅ Zotero 笔记已写入")
    except Exception as e:
        print(f"⚠️  写入 Zotero 笔记失败: {e}", file=sys.stderr)

    # 写入标签
    if tags:
        try:
            zc.add_tags(item_key, tags)
            print(f"✅ 标签已写入: {tags}")
        except Exception as e:
            print(f"⚠️  写入标签失败: {e}", file=sys.stderr)

    # 添加 Markdown 链接附件
    try:
        zc.add_linked_markdown(item_key, md_path, title)
        print(f"✅ Zotero 附件链接已创建")
    except Exception as e:
        print(f"⚠️  创建附件链接失败: {e}", file=sys.stderr)

    print(f"\n🎉 保存完成: {item_key}")


if __name__ == '__main__':
    main()
