"""
github_models_client.py — 多模型 LLM 客户端
支持：
  - GitHub Models (gpt-4o, gpt-4o-mini 等 OpenAI 兼容模型)
  - Anthropic API (claude-haiku-4-5, claude-sonnet-4-6 等)
自动选择 provider，遇到 413 时逐步截断重试并报告阅读比例
"""

import os
import re
import json
import requests
import yaml


def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def load_skill_prompt(skill_path=None):
    """
    从 SKILL.md 提取完整 System Prompt（包含分析模板）。
    提取范围：frontmatter 之后的所有内容。
    """
    if skill_path is None:
        skill_path = os.path.join(os.path.dirname(__file__), '..', 'skills', 'read-paper', 'SKILL.md')
    with open(skill_path, 'r', encoding='utf-8') as f:
        content = f.read()
    stripped = re.sub(r'^---\n.*?\n---\n', '', content, count=1, flags=re.DOTALL)
    return stripped.strip()


# ---- Provider 检测 ----

def _is_anthropic_model(model_name):
    """判断是否为 Anthropic Claude 模型"""
    return model_name.startswith('claude-')


# ---- Anthropic API 调用 ----

def _call_anthropic(api_key, model, system_prompt, user_message, max_tokens=2048, temperature=0.3):
    """调用 Anthropic API（claude-haiku-4-5, claude-sonnet-4-6 等）"""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return msg.content[0].text


# ---- GitHub Models API 调用（OpenAI 兼容）----

def _call_github_models(token, endpoint, model, system_prompt, user_message, max_tokens=2048, temperature=0.3):
    """调用 GitHub Models REST API"""
    url = f"{endpoint}/chat/completions"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code == 413:
        raise ValueError("__413__")
    if resp.status_code == 401:
        raise RuntimeError("GitHub Token 无效或过期，请检查 config.yaml 中的 token")
    if resp.status_code == 429:
        raise RuntimeError("GitHub Models API 请求频率超限（rate limit），请稍后再试")
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


class GitHubModelsClient:
    def __init__(self, config=None, model_override=None):
        if config is None:
            config = load_config()
        gm_cfg = config['github_models']
        self.token = gm_cfg['token']
        self.endpoint = gm_cfg.get('endpoint', 'https://models.inference.ai.azure.com')
        self.model = model_override or gm_cfg.get('model', 'gpt-4o')
        self.max_tokens = gm_cfg.get('max_tokens', 2048)
        self.temperature = gm_cfg.get('temperature', 0.3)
        self.skill_system_prompt = load_skill_prompt()

        # Anthropic 配置（可选）
        ant_cfg = config.get('anthropic', {})
        self.anthropic_key = ant_cfg.get('api_key', '')

        # 模型切换阈值
        fb_cfg = config.get('model_fallback', {})
        self.fallback_enabled = fb_cfg.get('enabled', True)
        self.fallback_threshold = fb_cfg.get('threshold_chars', 80000)
        self.fallback_model = fb_cfg.get('large_context_model', self.model)

    def analyze_paper(self, metadata, pdf_text=None, original_pdf_chars=None):
        """
        调用 LLM 分析论文。
        遇到 413 负载过大时自动截断重试，并报告阅读比例。

        Args:
            metadata: 论文元数据
            pdf_text: PDF 文本（可已经被部分截断）
            original_pdf_chars: PDF 原始总字符数（用于计算阅读比例）

        Returns:
            tuple: (analysis_text, read_ratio, actual_chars_sent)
                   read_ratio: 0.0-1.0，实际阅读比例；1.0 = 全文
        """
        original_len = original_pdf_chars or (len(pdf_text) if pdf_text else 0)

        # 自动模型切换
        model_to_use = self.model
        if self.fallback_enabled and pdf_text and len(pdf_text) > self.fallback_threshold:
            if self.fallback_model != self.model:
                model_to_use = self.fallback_model
                print(f"  ⚡ 论文较长，切换到 {model_to_use}")

        current_text = pdf_text
        for attempt in range(4):
            user_message = self._build_user_message(metadata, current_text)
            try:
                result = self._call(model_to_use, user_message)
                # 计算实际阅读比例
                actual_chars = len(current_text) if current_text else 0
                ratio = (actual_chars / original_len) if original_len > 0 else 1.0
                return result, ratio, actual_chars
            except ValueError as e:
                if '__413__' in str(e) and current_text:
                    new_len = len(current_text) // 2
                    pct = int(new_len / original_len * 100) if original_len else 50
                    print(f"  ⚠️  负载过大，截断至 {new_len:,} 字符（原文 {pct}%）后重试...")
                    current_text = current_text[:new_len]
                else:
                    raise RuntimeError(str(e))
            except Exception as e:
                raise RuntimeError(str(e))

        # 最后兜底：仅用元数据
        print(f"  ⚠️  多次截断后仍失败，改用纯元数据分析（未读取 PDF）")
        user_message = self._build_user_message(metadata, None)
        result = self._call(model_to_use, user_message)
        return result, 0.0, 0

    def _call(self, model, user_message):
        """根据 model 名称自动选择 provider"""
        if _is_anthropic_model(model):
            if not self.anthropic_key:
                raise RuntimeError(
                    f"使用 Claude 模型需要在 config.yaml 中配置 anthropic.api_key\n"
                    f"获取方式：https://console.anthropic.com/settings/keys"
                )
            return _call_anthropic(
                self.anthropic_key, model,
                self.skill_system_prompt, user_message,
                self.max_tokens, self.temperature
            )
        else:
            return _call_github_models(
                self.token, self.endpoint, model,
                self.skill_system_prompt, user_message,
                self.max_tokens, self.temperature
            )

    def _build_user_message(self, metadata, pdf_text):
        parts = [
            "请分析以下论文：\n",
            f"**标题**: {metadata.get('title', '未知')}",
            f"**作者**: {metadata.get('authors', '未知')}",
            f"**年份**: {metadata.get('year', '未知')}",
            f"**期刊/会议**: {metadata.get('venue', '未知')}",
            f"**DOI**: {metadata.get('doi', '无')}",
        ]
        abstract = metadata.get('abstract', '').strip()
        if abstract:
            parts.append(f"\n**摘要**:\n{abstract}")
        if pdf_text:
            parts.append(f"\n---\n\n**论文全文**:\n\n{pdf_text}")
        else:
            parts.append('\n（注：未能提取 PDF 全文，请仅基于以上元数据进行分析，对未知内容标注"原文未提及"）')
        return '\n'.join(parts)

    def extract_tags_from_analysis(self, analysis_text, valid_tags=None):
        """提取标签：先找 JSON 数组，兜底用关键词匹配"""
        found_tags = []
        matches = re.findall(r'\[([^\[\]]{5,200})\]', analysis_text)
        for match in matches:
            try:
                tags = json.loads(f'[{match}]')
                if tags and all(isinstance(t, str) for t in tags):
                    if valid_tags:
                        tags = [t for t in tags if t in valid_tags]
                    if tags:
                        found_tags = tags
                        break
            except (json.JSONDecodeError, ValueError):
                continue
        if not found_tags and valid_tags:
            found_tags = [t for t in valid_tags if t in analysis_text][:8]
        return found_tags
