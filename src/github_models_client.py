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

        # 预定义标签（严格白名单）
        tags_cfg = config.get('tags', {})
        self.valid_tags = (
            tags_cfg.get('domain', []) +
            tags_cfg.get('method', []) +
            tags_cfg.get('status', [])
        )

        # 模型切换阈值
        fb_cfg = config.get('model_fallback', {})
        self.fallback_enabled = fb_cfg.get('enabled', True)
        self.fallback_threshold = fb_cfg.get('threshold_chars', 80000)
        self.fallback_model = fb_cfg.get('large_context_model', self.model)

    def analyze_paper(self, metadata, pdf_text=None, original_pdf_chars=None):
        """
        调用 LLM 分析论文。
        策略：
          - 短文（< CHUNK_LIMIT）：单次提交
          - 长文（>= CHUNK_LIMIT）：分块提交（分析前半 + 后半）再合并
          - 遇到 413 时自动缩小块大小重试

        Returns:
            tuple: (analysis_text, read_ratio, actual_chars_sent)
        """
        CHUNK_LIMIT = 25000   # 单次安全提交上限（字符）

        original_len = original_pdf_chars or (len(pdf_text) if pdf_text else 0)

        # 自动模型切换
        model_to_use = self.model
        if self.fallback_enabled and pdf_text and len(pdf_text) > self.fallback_threshold:
            if self.fallback_model != self.model:
                model_to_use = self.fallback_model
                print(f"  ⚡ 论文较长，切换到 {model_to_use}")

        if not pdf_text or len(pdf_text) <= CHUNK_LIMIT:
            # 短文：单次分析
            result, ratio, actual = self._analyze_single(
                model_to_use, metadata, pdf_text, original_len
            )
        else:
            # 长文：分块分析
            result, ratio, actual = self._analyze_chunked(
                model_to_use, metadata, pdf_text, original_len, CHUNK_LIMIT
            )

        return self._strip_code_fences(result), ratio, actual

    def _analyze_single(self, model, metadata, pdf_text, original_len):
        """单次提交分析，遇到 413 自动缩小文本重试"""
        current_text = pdf_text
        for attempt in range(4):
            user_message = self._build_user_message(metadata, current_text)
            try:
                result = self._call(model, user_message)
                actual_chars = len(current_text) if current_text else 0
                ratio = (actual_chars / original_len) if original_len > 0 else 1.0
                return result, ratio, actual_chars
            except ValueError as e:
                if '__413__' in str(e) and current_text:
                    new_len = len(current_text) // 2
                    pct = int(new_len / original_len * 100) if original_len else 50
                    print(f"  ⚠️  负载过大，缩减至 {new_len:,} 字符（原文 {pct}%）后重试...")
                    current_text = current_text[:new_len]
                else:
                    raise RuntimeError(str(e))
            except Exception as e:
                raise RuntimeError(str(e))

        # 兜底：纯元数据
        print(f"  ⚠️  多次重试失败，改用纯元数据分析")
        result = self._call(model, self._build_user_message(metadata, None))
        return result, 0.0, 0

    def _analyze_chunked(self, model, metadata, pdf_text, original_len, chunk_limit):
        """
        分块分析长文献：
          第1块（前半）→ 提取问题/Insight/方法
          第2块（后半）→ 提取实验/结果/局限
          最终合并     → 生成完整结构化报告
        """
        total_chars = len(pdf_text)
        mid = total_chars // 2
        chunk1 = pdf_text[:mid]
        chunk2 = pdf_text[mid:]

        # 如果单块仍超限，缩小到 chunk_limit
        if len(chunk1) > chunk_limit:
            chunk1 = chunk1[:chunk_limit]
        if len(chunk2) > chunk_limit:
            chunk2 = chunk2[-chunk_limit:]   # 取后半的末尾（结论区域）

        actual_chars = len(chunk1) + len(chunk2)
        ratio = actual_chars / original_len if original_len else 1.0
        pct = int(ratio * 100)
        print(f"  📚 分块分析：块1={len(chunk1):,}字符 + 块2={len(chunk2):,}字符 = {pct}% 覆盖率")

        # 第1块：问题/背景/方法
        prompt1 = (
            f"你正在阅读论文《{metadata.get('title','?')}》的【前半部分】。\n"
            f"请仅基于此部分内容，提取：\n"
            f"1. 当前领域的核心问题与挑战\n"
            f"2. 作者的核心 Insight（洞察）\n"
            f"3. 方法设计（如何用 Insight 解决问题）\n\n"
            f"输出纯文本，不要用代码块包裹，标注「前半部分分析」。\n\n"
            f"--- 论文前半部分 ---\n{chunk1}"
        )
        print(f"  🤖 分析第1块（前半：问题/Insight/方法）...")
        analysis1 = self._call_with_retry(model, prompt1)

        # 第2块：实验/结果/局限
        prompt2 = (
            f"你正在阅读论文《{metadata.get('title','?')}》的【后半部分】。\n"
            f"请仅基于此部分内容，提取：\n"
            f"1. 实验设计与贡献的对应关系（Metrics、对比基线）\n"
            f"2. 本质启发（可迁移的方法论）\n"
            f"3. 局限性（方法的根本限制）\n\n"
            f"输出纯文本，不要用代码块包裹，标注「后半部分分析」。\n\n"
            f"--- 论文后半部分 ---\n{chunk2}"
        )
        print(f"  🤖 分析第2块（后半：实验/结果/局限）...")
        analysis2 = self._call_with_retry(model, prompt2)

        # 合并：生成最终结构化报告
        merge_prompt = (
            f"请将以下两段对论文《{metadata.get('title','?')}》的分段分析，"
            f"整合为一份完整的、符合格式要求的论文分析报告。\n"
            f"直接输出报告内容，不要用代码块包裹，不要有多余前言。\n\n"
            f"=== 前半部分分析 ===\n{analysis1}\n\n"
            f"=== 后半部分分析 ===\n{analysis2}"
        )
        print(f"  🤖 合并生成最终报告...")
        final = self._call_with_retry(model, merge_prompt)
        return final, ratio, actual_chars

    def _call_with_retry(self, model, user_message):
        """调用 LLM，遇到 413 缩短消息重试"""
        for attempt in range(3):
            try:
                return self._call(model, user_message)
            except ValueError as e:
                if '__413__' in str(e):
                    # 截断 user_message 末尾 30%
                    user_message = user_message[:int(len(user_message) * 0.7)]
                    print(f"    ↩️  负载过大，缩减消息后重试...")
                else:
                    raise RuntimeError(str(e))
            except Exception as e:
                raise RuntimeError(str(e))
        raise RuntimeError("多次重试后仍无法完成 LLM 调用")

    @staticmethod
    def _strip_code_fences(text):
        """移除 LLM 输出中的代码围栏（```markdown ... ``` 等）"""
        import re
        text = re.sub(r'^```[a-zA-Z]*\n?', '', text.strip())
        text = re.sub(r'\n?```$', '', text.strip())
        return text.strip()

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
            "请分析以下论文。**输出格式要求：直接输出 Markdown 正文，禁止用代码块（```）包裹整个输出。**\n",
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

        # 严格标签约束：注入可用标签白名单
        if self.valid_tags:
            tag_list = json.dumps(self.valid_tags, ensure_ascii=False)
            parts.append(
                f"\n---\n\n**【标签选择——严格要求】**\n"
                f"请从下方白名单中选出 2-5 个最贴切的标签，输出为 JSON 数组。\n"
                f"⚠️ 只能使用白名单中的原文标签，禁止创造任何新标签，禁止修改标签文字。\n"
                f"白名单：{tag_list}\n"
                f"输出格式示例（放在分析末尾）：\n"
                f'**推荐标签**: ["四足机器人", "强化学习", "真实实验"]'
            )
        return '\n'.join(parts)

    def extract_tags_from_analysis(self, analysis_text, valid_tags=None):
        """
        从 LLM 输出中提取标签，严格过滤到白名单。
        策略：
          1. 找 JSON 数组 → 过滤到 valid_tags
          2. 找「推荐标签:」行 → 解析其中标签 → 过滤到 valid_tags
          3. 任何非白名单标签直接丢弃（不做关键词匹配，避免误判）
        """
        whitelist = set(valid_tags or self.valid_tags)
        found_tags = []

        # 策略1: 找 JSON 数组（如 ["A", "B", "C"]）
        matches = re.findall(r'\[([^\[\]]{2,300})\]', analysis_text)
        for match in matches:
            try:
                tags = json.loads(f'[{match}]')
                if tags and all(isinstance(t, str) for t in tags):
                    filtered = [t.strip() for t in tags if t.strip() in whitelist]
                    if filtered:
                        found_tags = filtered
                        break
            except (json.JSONDecodeError, ValueError):
                continue

        # 策略2: 找「推荐标签」行，逐个词匹配白名单
        if not found_tags:
            tag_line_match = re.search(
                r'(?:推荐标签|建议标签|标签)[：:]\s*(.+)', analysis_text
            )
            if tag_line_match:
                line = tag_line_match.group(1)
                # 去掉 markdown 格式，按常见分隔符切分
                line = re.sub(r'[`\[\]"\'【】]', ' ', line)
                candidates = re.split(r'[,，、\s]+', line)
                found_tags = [c.strip() for c in candidates if c.strip() in whitelist]

        # 策略3: 在整个文本里逐一精确子串匹配白名单（兜底）
        # 中文标签不需要词边界，直接子串匹配即可（标签本身都是专业词汇，误判率极低）
        if not found_tags and whitelist:
            for tag in whitelist:
                if tag in analysis_text:
                    found_tags.append(tag)
            found_tags = found_tags[:5]  # 最多5个

        return found_tags
