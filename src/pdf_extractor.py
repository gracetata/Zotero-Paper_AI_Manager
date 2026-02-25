"""
pdf_extractor.py - PDF文本提取（全文策略）
提取论文所有页面，不跳过任何内容
字符数上限仅作安全保护（gpt-4o 128k上下文可处理典型学术论文全文）
"""

import fitz  # PyMuPDF
import os


def extract_all_pages(pdf_path, max_chars=150000):
    """
    提取 PDF 全文（所有页面顺序提取）。

    gpt-4o 支持 128k tokens ≈ 384k 英文字符。
    学术论文通常 10-60 页 ≈ 2-15 万字符，完全在上下文范围内。

    Args:
        pdf_path: PDF 文件路径
        max_chars: 字符安全上限（默认 150,000，约 50,000 tokens）

    Returns:
        tuple: (text: str, page_count: int, was_truncated: bool)
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return None, 0, False

    try:
        doc = fitz.open(pdf_path)
        total = len(doc)
        text_parts = []
        total_chars = 0

        for i in range(total):
            page_text = f"[第 {i+1}/{total} 页]\n{doc[i].get_text()}"
            if total_chars + len(page_text) > max_chars:
                # 只在真正超长时才截断（正常学术论文不会触发）
                remaining = max_chars - total_chars
                if remaining > 200:
                    text_parts.append(page_text[:remaining])
                text_parts.append(f"\n[... 第 {i+1}~{total} 页因超出字符上限已截断 ...]")
                doc.close()
                return '\n\n'.join(text_parts), total, True
            text_parts.append(page_text)
            total_chars += len(page_text)

        doc.close()
        return '\n\n'.join(text_parts), total, False

    except Exception as e:
        print(f"[ERROR] PDF提取失败 ({pdf_path}): {e}")
        return None, 0, False


# 保持旧接口兼容
def extract_key_sections(pdf_path, front_pages=5, tail_pages=3, max_chars=150000):
    """兼容旧接口，现在直接提取全文"""
    text, _, _ = extract_all_pages(pdf_path, max_chars=max_chars)
    return text


def extract_text(pdf_path, max_pages=10, max_chars=12000):
    """兼容旧接口"""
    text, _, _ = extract_all_pages(pdf_path, max_chars=max_chars)
    return text


def get_page_count(pdf_path):
    """获取 PDF 总页数"""
    try:
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


def extract_abstract_only(pdf_path, max_chars=3000):
    """仅提取摘要部分（前2页）"""
    if not pdf_path or not os.path.exists(pdf_path):
        return None
    try:
        doc = fitz.open(pdf_path)
        text = '\n\n'.join(f"[第 {i+1} 页]\n{doc[i].get_text()}" for i in range(min(2, len(doc))))
        doc.close()
        return text[:max_chars]
    except Exception:
        return None
