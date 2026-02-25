# 🧠 ZoteroPaperManager — AI 驱动的文献自动分析系统

> 将论文加入 Zotero，AI 自动深度阅读、结构化分析、打标签、写笔记 —— 一气呵成

[![Python](https://img.shields.io/badge/Python-3.8+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## ✨ 核心优势

| 功能 | 说明 |
|------|------|
| 🤖 **AI 深度阅读** | 不是简单摘要——基于专属 Skill 框架，分析问题/Insight/方法/实验/局限性 |
| 📄 **全文提取** | 读取完整 PDF（非摘要截取），并**明确告知你读取了多少比例** |
| 🏷️ **智能标签** | 从预定义领域标签中自动匹配，直接写入 Zotero |
| 📝 **双写入** | 同时生成本地 Markdown 笔记 + Zotero 内置笔记 |
| 📑 **自动索引** | 维护 `INDEX.md` 全库目录，按年份分类，含标签和链接 |
| ⚡ **多模型支持** | GPT-4o（免费）+ Claude Haiku/Sonnet 4.6（大上下文长文献） |
| 🔁 **批量处理** | 一键分析整个现有文献库（277+ 篇） |
| 👁️ **监控自动化** | watchdog 监控 Zotero 数据库，新增论文秒级自动触发 |

---

## 🖥️ 效果演示

```
============================================================
🔍 正在处理: ABCD1234
  📄 标题: Learning to Walk in Minutes...
  👤 作者: Zhuang et al.
  📅 年份: 2024
  📖 PDF: 12 页，48,271 字符（全文）
  ✅ 全文已读取（48,271 字符，100%）
  🏷️  推荐标签: ['强化学习', '四足机器人', '真实实验']
  ✅ Markdown 已保存: notes/2024/Learning_to_Walk.md
  ✅ INDEX.md 已更新
  ✅ Zotero 笔记已写入
  ✅ Zotero 标签已写入
```

---

## 🏗️ 系统架构

```
Zotero 新增论文
      │
      ├─── [自动] watchdog 监控 zotero.sqlite
      └─── [手动] python paper_analyzer.py --recent 1
                │
                ▼
        Zotero Web API（读取元数据）
                │
                ▼
        PDF 全文提取（PyMuPDF）
                │
                ▼
        LLM 分析（GPT-4o / Claude）
        ┌───────────────────────────────────┐
        │  基于 Read Paper Skill 框架：      │
        │  1. 问题·挑战·Insight·方法设计    │
        │  2. 实验与贡献对应关系            │
        │  3. 本质启发与局限性              │
        └───────────────────────────────────┘
                │
       ┌────────┼────────┬──────────┐
       ▼        ▼        ▼          ▼
  Markdown   INDEX.md  Zotero    Zotero
   笔记文件   总目录     笔记      标签
```

---

## 📦 安装

```bash
git clone https://github.com/YOUR_USERNAME/PaperManager.git
cd PaperManager

# 安装依赖
pip install -r requirements.txt

# 复制并填写配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入你的 API Keys（见下方说明）
```

### 所需 API Keys

| Key | 获取地址 | 说明 |
|-----|---------|------|
| Zotero API Key | https://www.zotero.org/settings/keys | 个人库读+写权限 |
| GitHub Token | https://github.com/settings/tokens | 勾选 `models:read` |
| Anthropic Key（可选） | https://console.anthropic.com/settings/keys | 用于 Claude 大上下文 |

---

## 🚀 使用方法

```bash
cd src/

# 分析最近1篇（测试用）
python paper_analyzer.py --recent 1

# 分析指定论文
python paper_analyzer.py --key ZOTERO_ITEM_KEY

# 批量分析全库（智能跳过已处理）
python paper_analyzer.py --all

# 预览模式（不调用 LLM，不写入）
python paper_analyzer.py --dry-run --recent 5

# 指定模型（长文献推荐 Claude）
python paper_analyzer.py --recent 1 --model gpt-4o-mini
python paper_analyzer.py --recent 1 --model claude-haiku-4-5
python paper_analyzer.py --recent 1 --model claude-sonnet-4-6
```

### 自动监控模式（开机自启）

```bash
# 启动后台监控（Zotero 新增论文自动触发分析）
systemctl --user start zotero-watcher

# 查看运行状态
systemctl --user status zotero-watcher

# 停止
systemctl --user stop zotero-watcher
```

---

## 🏷️ 标签体系

可在 `config.yaml` 中自定义。默认预设：

**领域标签**: `下肢假肢` `膝关节` `踝关节` `外骨骼` `移动机器人` `四足机器人` `人形机器人` `强化学习` `最优控制` ...

**方法标签**: `综述` `真实实验` `仅仿真` `模仿学习` `基于模型` `Transformer` `扩散模型` ...

**状态标签**: `待读` `已读` `重要文献` `需复现` `背景阅读`

---

## 📁 项目结构

```
PaperManager/
├── skills/
│   └── read-paper/
│       └── SKILL.md          # AI 阅读框架（可自定义）
├── src/
│   ├── paper_analyzer.py     # 主入口（CLI）
│   ├── zotero_client.py      # Zotero API 封装
│   ├── pdf_extractor.py      # PDF 全文提取
│   ├── github_models_client.py  # LLM 多模型客户端
│   └── watch_zotero.py       # 自动监控
├── notes/                    # 分析输出（.gitignore 中）
│   ├── INDEX.md              # 全库索引
│   └── 2024/                 # 按年份分类
├── config.yaml               # 你的配置（含 API Key，不提交！）
├── config.example.yaml       # 配置模板（安全提交）
└── requirements.txt
```

---

## ⚠️ PDF 读取透明度

系统会**明确告知**每篇论文的实际读取比例：

- `✅ 全文已读取（48,271 字符，100%）` — 完整读取
- `⚠️ 仅读取了论文前 62% 内容（28,000/45,000 字符）` — 因 API 限制截断
  - 此时建议切换到 Claude：`--model claude-haiku-4-5`（200k 上下文，几乎不截断）

---

## 🔧 Read Paper Skill 框架

分析框架定义在 `skills/read-paper/SKILL.md`，结构化引导 AI 产出：

1. **问题与挑战** — 当前领域面临什么问题，作者的核心 Insight 是什么
2. **方法设计** — 如何利用 Insight 设计解决方案
3. **实验与贡献** — 实验如何对应贡献，Metrics 表现
4. **本质启发与局限** — 可迁移的启发，以及方法的根本限制

> 可直接修改 `SKILL.md` 定制分析风格，无需改代码

---

## 📄 License

MIT License © 2025
