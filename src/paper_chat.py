"""
paper_chat.py — 论文 AI 追问对话模式

基于已有论文分析加载上下文，在终端进行多轮追问。
用法:
  python paper_chat.py --key ZOTERO_ITEM_KEY     # 用 Zotero key 加载论文
  python paper_chat.py --md path/to/note.md      # 直接加载 Markdown 分析文件
  python paper_chat.py --key ITEM_KEY --model claude-haiku-4-5  # 指定模型
"""

import os
import sys
import re
import yaml
import argparse

sys.path.insert(0, os.path.dirname(__file__))

from zotero_client import ZoteroClient
from pdf_extractor import extract_all_pages
from github_models_client import GitHubModelsClient


WELCOME = """
╔══════════════════════════════════════════════════════════════╗
║              📖  论文追问模式  Paper Chat                    ║
║  输入你的问题，直接回车提交；输入 q / exit 退出              ║
║  输入 /clear 清空对话历史；输入 /info 查看论文信息           ║
╚══════════════════════════════════════════════════════════════╝
"""


def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def find_markdown_for_key(item_key, notes_dir):
    """在 notes/ 目录下搜索包含指定 zotero_key 的 Markdown 文件"""
    for root, dirs, files in os.walk(notes_dir):
        for fname in files:
            if not fname.endswith('.md') or fname == 'INDEX.md':
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, 'r', encoding='utf-8') as f:
                head = f.read(512)
            if f'zotero_key: {item_key}' in head:
                return fpath
    return None


def load_context_from_markdown(md_path):
    """从 Markdown 文件提取 frontmatter 元数据和分析内容"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取 frontmatter
    metadata = {}
    fm_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                metadata[k.strip()] = v.strip().strip('"')
        body = content[fm_match.end():]
    else:
        body = content

    return metadata, body


def build_system_prompt(metadata, prior_analysis, pdf_text=None):
    """构建对话 system prompt，包含论文上下文"""
    parts = [
        "你是一位专业的学术论文分析助手。用户将就以下论文向你提问，请基于论文内容和已有分析给出精准回答。",
        "",
        "【论文基本信息】",
        f"标题: {metadata.get('title', '未知')}",
        f"作者: {metadata.get('authors', '未知')}",
        f"年份: {metadata.get('year', '未知')}",
        f"期刊/会议: {metadata.get('venue', '未知')}",
        "",
    ]
    if prior_analysis:
        parts += [
            "【已有 AI 分析摘要（可供参考）】",
            prior_analysis[:3000],  # 防止 system prompt 过长
            "",
        ]
    if pdf_text:
        parts += [
            "【论文全文（部分）】",
            pdf_text[:8000],
            "",
        ]
    parts.append(
        "请用中文回答。如果问题超出论文范围，请诚实说明，不要捏造内容。"
    )
    return '\n'.join(parts)


def chat_loop(system_prompt, llm_client, metadata):
    """多轮对话主循环"""
    print(WELCOME)
    print(f"📄 论文: {metadata.get('title', '?')[:80]}")
    print(f"👤 作者: {metadata.get('authors', '?')[:60]}")
    print(f"🤖 模型: {llm_client.model}")
    print()

    conversation = []  # 对话历史 [{"role": ..., "content": ...}]

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 退出追问模式")
            break

        if not user_input:
            continue
        if user_input.lower() in ('q', 'exit', 'quit', '退出'):
            print("👋 退出追问模式")
            break
        if user_input == '/clear':
            conversation.clear()
            print("✅ 对话历史已清空\n")
            continue
        if user_input == '/info':
            print(f"\n📄 标题: {metadata.get('title', '?')}")
            print(f"👤 作者: {metadata.get('authors', '?')}")
            print(f"📅 年份: {metadata.get('year', '?')}")
            print(f"🏛️  期刊: {metadata.get('venue', '?')}")
            print(f"🤖 模型: {llm_client.model}\n")
            continue

        conversation.append({"role": "user", "content": user_input})

        # 调用 LLM（带对话历史）
        print("\n🤖 AI: ", end='', flush=True)
        try:
            reply = _call_with_history(llm_client, system_prompt, conversation)
            print(reply)
            print()
            conversation.append({"role": "assistant", "content": reply})
        except Exception as e:
            print(f"\n❌ 调用失败: {e}\n")
            conversation.pop()  # 失败时移除用户消息


def _call_with_history(llm_client, system_prompt, conversation):
    """调用 LLM，支持多轮对话历史"""
    from github_models_client import _is_anthropic_model

    model = llm_client.model

    if _is_anthropic_model(model):
        import anthropic
        if not llm_client.anthropic_key:
            raise RuntimeError("需要在 config.yaml 中配置 anthropic.api_key 才能使用 Claude 模型")
        client = anthropic.Anthropic(api_key=llm_client.anthropic_key)
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0.3,
            system=system_prompt,
            messages=conversation,
        )
        return msg.content[0].text
    else:
        import requests as req
        url = f"{llm_client.endpoint}/chat/completions"
        headers = {"Authorization": f"Bearer {llm_client.token}", "Content-Type": "application/json"}
        messages = [{"role": "system", "content": system_prompt}] + conversation
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.3,
        }
        resp = req.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code == 401:
            raise RuntimeError("GitHub Token 无效")
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content']


def main():
    parser = argparse.ArgumentParser(
        description='论文 AI 追问对话',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python paper_chat.py --key ABCD1234
  python paper_chat.py --md ../notes/2024/MyPaper.md
  python paper_chat.py --key ABCD1234 --model claude-haiku-4-5
  python paper_chat.py --key ABCD1234 --no-pdf    # 不加载 PDF，只用已有分析
        """
    )
    parser.add_argument('--key', type=str, help='Zotero item key')
    parser.add_argument('--md', type=str, help='直接指定已有分析 Markdown 文件路径')
    parser.add_argument('--model', type=str, help='指定模型（如 claude-haiku-4-5, gpt-4o）')
    parser.add_argument('--no-pdf', action='store_true', help='不加载 PDF 全文（加快速度）')
    parser.add_argument('--config', type=str, help='指定 config.yaml 路径')
    args = parser.parse_args()

    if not args.key and not args.md:
        parser.print_help()
        sys.exit(1)

    # 加载配置
    if args.config:
        with open(args.config) as f:
            config = yaml.safe_load(f)
    else:
        config = load_config()

    notes_dir = config['output']['notes_dir']

    # 初始化 LLM 客户端
    llm_client = GitHubModelsClient(config, model_override=args.model)

    # 加载 Markdown 分析
    md_path = args.md
    if not md_path and args.key:
        md_path = find_markdown_for_key(args.key, notes_dir)
        if md_path:
            print(f"✅ 找到已有分析: {md_path}")
        else:
            print(f"ℹ️  未找到已有分析文件，将直接加载 PDF 和元数据")

    metadata = {}
    prior_analysis = ''
    if md_path and os.path.exists(md_path):
        metadata, prior_analysis = load_context_from_markdown(md_path)

    # 如果有 Zotero key，补充元数据
    if args.key and not metadata.get('title'):
        print("🔍 从 Zotero 获取元数据...")
        try:
            zotero_client = ZoteroClient(config)
            item = zotero_client.get_item(args.key)
            metadata = zotero_client.get_item_metadata(item)
        except Exception as e:
            print(f"⚠️  获取 Zotero 元数据失败: {e}")

    # 可选：加载 PDF 全文
    pdf_text = None
    if not args.no_pdf and args.key:
        try:
            zotero_client = ZoteroClient(config)
            pdf_path = zotero_client.find_local_pdf(args.key)
            if not pdf_path:
                pdf_path = zotero_client.find_pdf_via_attachments(args.key)
            if pdf_path:
                pdf_cfg = config.get('pdf', {})
                pdf_text, pages, _ = extract_all_pages(pdf_path, max_chars=pdf_cfg.get('max_chars', 150000))
                print(f"📖 PDF 已加载: {pages} 页，{len(pdf_text):,} 字符")
        except Exception as e:
            print(f"⚠️  PDF 加载失败（对话仍可继续）: {e}")

    if not metadata.get('title') and not prior_analysis:
        print("❌ 无法加载论文信息，请检查 --key 或 --md 参数")
        sys.exit(1)

    # 构建 system prompt 并开始对话
    system_prompt = build_system_prompt(metadata, prior_analysis, pdf_text)
    chat_loop(system_prompt, llm_client, metadata)


if __name__ == '__main__':
    main()
