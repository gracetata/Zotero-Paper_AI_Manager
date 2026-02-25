"""
paper_analyzer.py — 主入口脚本
论文分析流程编排：读取 Zotero 条目 → 提取 PDF → LLM 分析 → 保存输出

用法:
  python paper_analyzer.py --all              # 处理所有未分析的论文（全库批量）
  python paper_analyzer.py --key ITEM_KEY     # 处理指定 Zotero 条目
  python paper_analyzer.py --recent 5         # 处理最近5篇论文
  python paper_analyzer.py --dry-run          # 仅预览，不写入
"""

import os
import sys
import json
import yaml
import argparse
import re
import time
from datetime import datetime
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, os.path.dirname(__file__))

from zotero_client import ZoteroClient
from pdf_extractor import extract_all_pages, get_page_count
from github_models_client import GitHubModelsClient


# ---- 配置加载 ----

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


# ---- 文件名清理 ----

def safe_filename(title, max_len=80):
    """将论文标题转换为合法文件名"""
    name = re.sub(r'[^\w\s\-]', '', title)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:max_len]


# ---- Markdown 输出 ----

def save_analysis_markdown(analysis_text, metadata, output_dir):
    """
    保存分析结果为 Markdown 文件。
    返回保存路径（相对于 notes 目录）。
    """
    year = metadata.get('year') or datetime.now().strftime('%Y')
    year_dir = os.path.join(output_dir, str(year))
    os.makedirs(year_dir, exist_ok=True)

    fname = safe_filename(metadata.get('title', metadata['key'])) + '.md'
    filepath = os.path.join(year_dir, fname)

    header = f"""---
zotero_key: {metadata['key']}
title: "{metadata.get('title', '')}"
authors: "{metadata.get('authors', '')}"
year: "{metadata.get('year', '')}"
venue: "{metadata.get('venue', '')}"
doi: "{metadata.get('doi', '')}"
analyzed_at: "{datetime.now().isoformat()}"
---

"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(header + analysis_text)

    print(f"  ✅ Markdown 已保存: {filepath}")
    return filepath


# ---- INDEX.md 更新 ----

def update_index(index_file, metadata, tags, analysis_path, notes_dir):
    """在 INDEX.md 中添加或更新论文记录"""
    # 计算相对路径
    rel_path = os.path.relpath(analysis_path, notes_dir)
    tag_str = ' '.join([f'`{t}`' for t in tags]) if tags else ''
    year = metadata.get('year', '?')
    title = metadata.get('title', metadata['key'])
    authors = metadata.get('authors', '')
    # 截断作者（只显示第一作者 et al.）
    if '; ' in authors:
        first_author = authors.split('; ')[0]
        authors_short = f"{first_author} et al."
    else:
        authors_short = authors

    new_row = f"| [{title}]({rel_path}) | {authors_short} | {year} | {tag_str} |\n"

    if not os.path.exists(index_file):
        # 创建新的 INDEX.md
        with open(index_file, 'w', encoding='utf-8') as f:
            f.write("# 📚 论文阅读索引\n\n")
            f.write(f"> 自动生成 · 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write("| 标题 | 作者 | 年份 | 标签 |\n")
            f.write("|------|------|------|------|\n")
            f.write(new_row)
        print(f"  ✅ INDEX.md 已创建: {index_file}")
        return

    # 读取现有内容
    with open(index_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 更新时间戳
    content = re.sub(
        r'> 自动生成 · 最后更新: .+\n',
        f'> 自动生成 · 最后更新: {datetime.now().strftime("%Y-%m-%d %H:%M")}\n',
        content
    )

    # 检查是否已存在此条目（通过 zotero_key 查找）
    key = metadata['key']
    if key in content:
        print(f"  ℹ️  INDEX.md 中已有此条目 ({key})，跳过")
        return

    # 在表格末尾添加新行
    with open(index_file, 'w', encoding='utf-8') as f:
        f.write(content.rstrip() + '\n' + new_row)

    print(f"  ✅ INDEX.md 已更新")


# ---- 阅读状态声明（嵌入分析报告顶部）----

def _build_read_status_note(read_ratio, actual_chars, original_chars, total_pages, model_name):
    """构建阅读状态声明，嵌入在分析报告顶部"""
    if original_chars == 0:
        return f"> 🤖 **分析模型**: {model_name}  \n> ⚠️ **读取状态**: 未找到 PDF，仅基于元数据和摘要分析"
    if read_ratio >= 0.99:
        return (
            f"> 🤖 **分析模型**: {model_name}  \n"
            f"> ✅ **读取状态**: 已读取全文（{actual_chars:,} 字符 / {total_pages} 页）"
        )
    pct = int(read_ratio * 100)
    return (
        f"> 🤖 **分析模型**: {model_name}  \n"
        f"> ⚠️ **读取状态**: 仅读取了论文前 **{pct}%** 内容"
        f"（{actual_chars:,} / {original_chars:,} 字符 · {total_pages} 页）  \n"
        f"> 💡 **提示**: 如需全文分析，可在 config.yaml 中配置 `anthropic.api_key` 并使用 Claude 模型（`--model claude-sonnet-4-6`）"
    )


# ---- 核心流程 ----

def process_item(item_key, zotero_client, llm_client, config, dry_run=False):
    """处理单篇论文的完整分析流程"""
    print(f"\n{'='*60}")
    print(f"🔍 正在处理: {item_key}")

    # 1. 获取 Zotero 元数据
    try:
        item = zotero_client.get_item(item_key)
        metadata = zotero_client.get_item_metadata(item)
    except Exception as e:
        print(f"  ❌ 获取 Zotero 元数据失败: {e}")
        return False

    print(f"  📄 标题: {metadata['title']}")
    print(f"  👤 作者: {metadata['authors']}")
    print(f"  📅 年份: {metadata['year']}")

    # 2. 查找并提取 PDF
    pdf_path = zotero_client.find_local_pdf(item_key)
    if not pdf_path:
        pdf_path = zotero_client.find_pdf_via_attachments(item_key)

    pdf_text = None
    original_pdf_chars = 0
    total_pages = 0
    if pdf_path:
        pdf_cfg = config.get('pdf', {})
        pdf_text, total_pages, was_truncated = extract_all_pages(
            pdf_path,
            max_chars=pdf_cfg.get('max_chars', 150000)
        )
        if pdf_text:
            original_pdf_chars = len(pdf_text)
            trunc_note = " ⚠️ (文件超大，已安全截断)" if was_truncated else "（全文）"
            print(f"  📖 PDF: {total_pages} 页，{original_pdf_chars:,} 字符 {trunc_note}")
        else:
            print(f"  ⚠️  PDF 提取失败，将仅使用元数据")
    else:
        print(f"  ⚠️  未找到本地 PDF，将仅使用元数据")

    if dry_run:
        print(f"  [dry-run] 跳过 LLM 调用和写入操作")
        return True

    # 3. 调用 LLM 分析（返回 analysis + 实际阅读比例）
    print(f"  🤖 调用 {llm_client.model} 分析中...")
    try:
        analysis, read_ratio, actual_chars = llm_client.analyze_paper(
            metadata, pdf_text, original_pdf_chars=original_pdf_chars
        )
    except RuntimeError as e:
        print(f"  ❌ LLM 分析失败: {e}")
        return False

    # 告知用户实际阅读了多少
    if pdf_text:
        if read_ratio >= 0.99:
            print(f"  ✅ 全文已读取（{actual_chars:,} 字符，100%）")
        elif read_ratio > 0:
            pct = int(read_ratio * 100)
            print(f"  ⚠️  仅读取了论文前 {pct}% 内容（{actual_chars:,}/{original_pdf_chars:,} 字符）"
                  f" — 后半部分未纳入分析，建议切换更大上下文模型")
        else:
            print(f"  ⚠️  未能读取 PDF，分析仅基于元数据和摘要")

    # 在分析文本开头插入阅读状态声明
    read_status_note = _build_read_status_note(read_ratio, actual_chars, original_pdf_chars, total_pages, llm_client.model)
    analysis_with_note = read_status_note + '\n\n' + analysis

    # 4. 提取标签
    all_valid_tags = (
        config['tags'].get('domain', []) +
        config['tags'].get('method', []) +
        config['tags'].get('status', [])
    )
    tags = llm_client.extract_tags_from_analysis(analysis, valid_tags=all_valid_tags)
    if not any(t in config['tags'].get('status', []) for t in tags):
        tags.append('已读')
    print(f"  🏷️  推荐标签: {tags}")

    # 5. 保存 Markdown（含阅读状态声明）
    notes_dir = config['output']['notes_dir']
    analysis_path = save_analysis_markdown(analysis_with_note, metadata, notes_dir)

    # 6. 更新 INDEX.md
    update_index(config['output']['index_file'], metadata, tags, analysis_path, notes_dir)

    # 7. 将 Markdown 以「链接文件」方式挂到 Zotero 条目
    try:
        att_key = zotero_client.add_linked_markdown(item_key, analysis_path)
        print(f"  ✅ Markdown 已关联到 Zotero 附件 (key: {att_key})")
    except FileNotFoundError as e:
        print(f"  ⚠️  附件关联失败: {e}")
    except RuntimeError as e:
        print(f"  ⚠️  {e}")
    except Exception as e:
        print(f"  ⚠️  Zotero 附件关联失败（不影响其他写入）: {e}")

    # 8. 写入 Zotero 笔记
    try:
        zotero_client.add_note(item_key, analysis_with_note)
        print(f"  ✅ Zotero 笔记已写入")
    except RuntimeError as e:
        print(f"  ⚠️  {e}")
    except Exception as e:
        print(f"  ⚠️  Zotero 笔记写入失败: {e}")

    # 9. 写入 Zotero 标签
    try:
        zotero_client.add_tags(item_key, tags)
        print(f"  ✅ Zotero 标签已写入: {tags}")
    except RuntimeError as e:
        print(f"  ⚠️  {e}")
    except Exception as e:
        print(f"  ⚠️  Zotero 标签写入失败: {e}")

    return True


def load_processed_ids(processed_file):
    """加载已处理的条目 ID 集合"""
    if os.path.exists(processed_file):
        with open(processed_file, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_processed_id(processed_file, item_key):
    """记录已处理的条目 ID"""
    with open(processed_file, 'a') as f:
        f.write(item_key + '\n')


# ---- 命令行入口 ----

def main():
    parser = argparse.ArgumentParser(
        description='Zotero 论文自动分析工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python paper_analyzer.py --all                           # 处理所有未分析的新论文
  python paper_analyzer.py --key ABC123DE                 # 处理指定 Zotero 条目
  python paper_analyzer.py --recent 5                     # 处理最近5篇论文
  python paper_analyzer.py --dry-run --recent 3           # 预览（不写入）
  python paper_analyzer.py --recent 1 --model gpt-4o-mini # 使用轻量模型
  python paper_analyzer.py --recent 1 --model claude-haiku-4-5    # 使用 Claude Haiku
  python paper_analyzer.py --recent 1 --model claude-sonnet-4-6   # 使用 Claude Sonnet 4.6
        """
    )
    parser.add_argument('--key', type=str, help='处理指定的 Zotero item key')
    parser.add_argument('--all', action='store_true', help='处理所有未分析的新论文')
    parser.add_argument('--recent', type=int, metavar='N', help='处理最近 N 篇论文')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不调用 LLM 也不写入')
    parser.add_argument('--config', type=str, help='指定 config.yaml 路径')
    parser.add_argument('--model', type=str, metavar='MODEL',
                        help='覆盖 config.yaml 中的模型设置，如 gpt-4o / gpt-4o-mini / claude-haiku-4-5 / claude-sonnet-4-6')
    args = parser.parse_args()

    if not any([args.key, args.all, args.recent]):
        parser.print_help()
        sys.exit(1)

    # 加载配置
    if args.config:
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
    else:
        config = load_config()

    # 检查 API key 配置
    if config['zotero']['api_key'] == 'YOUR_ZOTERO_API_KEY':
        print("❌ 请先在 config.yaml 中配置 Zotero API Key")
        print("   获取方式：https://www.zotero.org/settings/keys")
        sys.exit(1)
    if config['github_models']['token'] == 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN':
        print("❌ 请先在 config.yaml 中配置 GitHub Personal Access Token")
        print("   获取方式：https://github.com/settings/tokens")
        sys.exit(1)

    # 初始化客户端
    print("🚀 初始化客户端...")
    zotero_client = ZoteroClient(config)
    llm_client = GitHubModelsClient(config, model_override=args.model)
    print(f"   使用模型: {llm_client.model}")

    processed_file = config.get('watchdog', {}).get(
        'processed_ids_file',
        os.path.join(os.path.dirname(__file__), '..', '.processed_ids')
    )

    success_count = 0
    fail_count = 0
    skip_count = 0

    if args.key:
        # 处理单个条目
        ok = process_item(args.key, zotero_client, llm_client, config, dry_run=args.dry_run)
        if ok:
            save_processed_id(processed_file, args.key)
            success_count += 1
        else:
            fail_count += 1

    elif args.all:
        # 全库批量处理（分页获取所有条目）
        print("📥 正在获取 Zotero 全库条目（分页加载）...")
        items = zotero_client.get_all_items()
        processed_ids = load_processed_ids(processed_file)
        total = len(items)
        print(f"📚 全库共 {total} 篇文献，已处理 {len(processed_ids)} 篇，"
              f"待处理 {total - len([i for i in items if i['data']['key'] in processed_ids])} 篇\n")

        for idx, item in enumerate(items, 1):
            key = item['data']['key']
            title = item['data'].get('title', key)[:50]
            if key in processed_ids:
                skip_count += 1
                continue

            print(f"[{idx}/{total}] ", end='', flush=True)
            ok = process_item(key, zotero_client, llm_client, config, dry_run=args.dry_run)
            if ok:
                if not args.dry_run:
                    save_processed_id(processed_file, key)
                success_count += 1
            else:
                fail_count += 1

            # 速率控制：每篇之间等待 3 秒，避免触发 GitHub Models rate limit
            if not args.dry_run and idx < total:
                time.sleep(3)

    elif args.recent:
        print(f"📥 获取最近 {args.recent} 篇论文...")
        items = zotero_client.get_recent_items(limit=args.recent)
        processed_ids = load_processed_ids(processed_file)
        total = len(items)

        for idx, item in enumerate(items, 1):
            key = item['data']['key']
            print(f"[{idx}/{total}] ", end='', flush=True)
            ok = process_item(key, zotero_client, llm_client, config, dry_run=args.dry_run)
            if ok:
                if not args.dry_run:
                    save_processed_id(processed_file, key)
                success_count += 1
            else:
                fail_count += 1

            if not args.dry_run and idx < total:
                time.sleep(3)

    print(f"\n{'='*60}")
    print(f"✅ 完成！成功: {success_count}  跳过: {skip_count}  失败: {fail_count}")


if __name__ == '__main__':
    main()
