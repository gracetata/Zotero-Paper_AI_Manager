# 🧠 Zotero-Paper_AI_Manager

> AI 驱动的 Zotero 文献自动分析系统：论文加入 Zotero → **VS Code 弹窗提醒** → Claude 深度阅读 → 结构化分析写回 Zotero —— 一气呵成

[![Python](https://img.shields.io/badge/Python-3.8+-blue)](https://python.org)
[![VS Code](https://img.shields.io/badge/VS%20Code-Extension-007ACC)](https://code.visualstudio.com)
[![GitHub Copilot](https://img.shields.io/badge/GitHub%20Copilot-Claude%20%7C%20GPT--4o-black)](https://github.com/features/copilot)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## ✨ 核心优势

| 功能 | 说明 |
|------|------|
| 🤖 **Copilot 驱动** | **无需额外 API Key**，直接用 GitHub Copilot 会员资格调用 Claude / GPT-4o |
| 🔔 **VS Code 弹窗提醒** | 新论文 PDF 下载后自动弹出通知，可选「查看进度」或「跳过」 |
| 📄 **深度结构化分析** | 基于专属 Skill 框架：问题/Insight/方法/实验/启发与局限性 |
| 📊 **读取透明** | 明确显示读取比例（如 100% / 62%），不静默截断 |
| 🏷️ **严格标签管理** | 仅使用预定义领域标签，三重过滤，直接写入 Zotero |
| 📎 **Markdown 附件** | 分析 Markdown 自动关联为 Zotero 条目附件，一键打开 |
| 💬 **论文追问对话** | `paper_chat.py` 支持多轮 AI 追问，终端直接对话 |
| 🔁 **批量处理** | 一键分析整个文献库（跳过已处理） |

---

## 🖥️ 工作流程

```
在 Zotero / 浏览器插件中保存论文
        │
        ▼
PDF 下载完成 → VS Code 检测到新文件
        │
        ▼
 ┌──────────────────────────────┐
 │  🔔 右下角弹窗通知            │
 │  "检测到新论文 (XXXXXXXX)    │
 │   开始 AI 分析..."           │
 │  [查看进度]  [跳过此次]       │
 └──────────────────────────────┘
        │
        ▼
  进度条通知（全程可见）
  ① 提取 PDF 文本
  ② Claude 分析（via Copilot）
  ③ 写回 Zotero
        │
        ▼
 ✅ 完成通知 → Zotero 笔记 + 标签 + Markdown 附件
```

---

## 🏗️ 系统架构

```
VS Code 扩展（文件监听）
        │
        ├─ pdf_to_text.py    → 提取 PDF 全文
        │
        ├─ vscode.lm API     → Claude / GPT-4o（via GitHub Copilot）
        │  ┌─────────────────────────────────────┐
        │  │  Read Paper Skill 分析框架：          │
        │  │  1. 领域问题与挑战 · Insight          │
        │  │  2. 方法设计                          │
        │  │  3. 实验与贡献                        │
        │  │  4. 本质启发与局限性                  │
        │  └─────────────────────────────────────┘
        │
        └─ save_analysis.py  → 写回 Zotero（笔记 + 标签 + Markdown）
                                      │
                           ┌──────────┼──────────┬──────────┐
                           ▼          ▼          ▼          ▼
                       Markdown   INDEX.md  Zotero    Zotero
                        笔记文件   总目录    附件链接   笔记+标签
                                                ↕
                                         paper_chat.py
                                         （多轮追问对话）
```

---

## 📦 安装

### 1. 克隆项目

```bash
git clone https://github.com/gracetata/Zotero-Paper_AI_Manager.git
cd Zotero-Paper_AI_Manager
pip install -r requirements.txt
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入 Zotero API Key 和 Library ID
```

### 2. 配置（config.yaml）

```yaml
zotero:
  api_key: "YOUR_ZOTERO_API_KEY"   # https://www.zotero.org/settings/keys
  library_id: "YOUR_LIBRARY_ID"    # 个人库：https://www.zotero.org/settings/keys 页面底部数字
  library_type: "user"
```

> ⚠️ 只需填写 **Zotero** 配置即可使用 VS Code 扩展模式。GitHub Token / Anthropic Key 仅命令行模式需要。

### 3. 安装 VS Code 扩展

```bash
code --install-extension vscode-extension/zotero-paper-ai-manager-1.0.0.vsix
```

重启 VS Code 后扩展自动激活，右下角状态栏出现 `$(eye) Paper AI: 监听中`。

---

## 🚀 使用方法

### 方式一：VS Code 扩展（推荐）

**自动监听新论文：**
- 扩展启动后自动监听 `~/Zotero/storage`（可在设置中自定义路径）
- 新 PDF 下载完成 → **右下角弹出通知** → 点击「查看进度」跟踪分析过程
- 分析完成 → 再次弹出完成通知

**命令面板（`Ctrl+Shift+P`）：**

| 命令 | 说明 |
|------|------|
| `Paper Manager: Analyze Paper by Zotero Key` | 手动输入 Key 分析指定论文 |
| `Paper Manager: Toggle Auto-Watch` | 开启/关闭自动监听 |
| `Paper Manager: Analyze All Unprocessed Papers` | 批量分析整个文献库 |

**VS Code 设置（`Ctrl+,` → 搜索 "Paper Manager"）：**

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `paperManager.model` | `claude-3.5-sonnet` | 分析用模型 |
| `paperManager.projectPath` | `~/Workspace/PaperManager` | 项目路径 |
| `paperManager.zoteroStoragePath` | `~/Zotero/storage` | Zotero 存储路径 |
| `paperManager.pythonPath` | `python3` | Python 路径 |

**模型选项（全部通过 Copilot 免费使用）：**
- `claude-3.5-sonnet` — 推荐，质量最高，上下文大
- `claude-3-opus` — 最强推理能力，速度较慢
- `claude-3-haiku` — 最快，适合快速浏览
- `gpt-4o` — OpenAI 旗舰，备选

---

### 方式二：命令行模式

```bash
cd src/

# 分析指定论文（用 Zotero Item Key）
python paper_analyzer.py --key LVSSLJLL

# 分析最近1篇
python paper_analyzer.py --recent 1

# 批量分析全库（跳过已处理）
python paper_analyzer.py --all

# 预览模式（不调用 LLM，不写入）
python paper_analyzer.py --dry-run --recent 5

# 指定模型
python paper_analyzer.py --key LVSSLJLL --model claude-haiku-4-5
```

> 命令行模式需要在 `config.yaml` 中配置 `github_models.token`（GitHub PAT，用于 GPT-4o）或 `anthropic.api_key`（用于 Claude）

---

### 💬 追问对话（paper_chat.py）

```bash
cd src/

# 用 Zotero Item Key 追问（自动加载分析 + PDF）
python paper_chat.py --key LVSSLJLL

# 只用已有分析追问（快速模式）
python paper_chat.py --key LVSSLJLL --no-pdf

# 用 Claude 模型
python paper_chat.py --key LVSSLJLL --model claude-haiku-4-5
```

**对话内置命令：**

| 命令 | 说明 |
|------|------|
| `/clear` | 清空对话历史 |
| `/info` | 显示当前论文和模型信息 |
| `q` / `exit` | 退出 |

---

## 🔑 如何找到 Zotero Item Key

**Zotero Item Key** 是 8 位字母数字（如 `LVSSLJLL`），三种获取方式：

① **从分析 Markdown frontmatter**（最直接）
```yaml
---
zotero_key: LVSSLJLL    ← 这就是 Item Key
title: "Paper Title..."
---
```

② **从 INDEX.md**：每条记录末尾的 `` `XXXXXXXX` ``

③ **从 Zotero 网页版**：`https://www.zotero.org/用户名/items` → 点击条目 → URL 末尾

---

## 🏷️ 标签体系

在 `config.yaml` 的 `tags:` 下自定义，AI 严格只使用这些标签：

```yaml
tags:
  - 下肢假肢
  - 膝关节
  - 踝关节
  - 外骨骼
  - 移动机器人
  - 四足机器人
  - 人形机器人
```

---

## 📁 项目结构

```
Zotero-Paper_AI_Manager/
├── vscode-extension/              # VS Code 扩展（核心）
│   ├── src/extension.ts          # 扩展主逻辑（TypeScript）
│   ├── package.json
│   └── zotero-paper-ai-manager-1.0.0.vsix   # 直接安装的扩展包
├── skills/
│   └── read-paper/SKILL.md       # AI 分析框架（可自定义）
├── src/
│   ├── paper_analyzer.py         # 命令行分析入口
│   ├── paper_chat.py             # 追问对话模式
│   ├── pdf_to_text.py            # PDF 文本提取（供扩展调用）
│   ├── save_analysis.py          # 分析写回 Zotero（供扩展调用）
│   ├── zotero_client.py          # Zotero API 封装
│   ├── github_models_client.py   # LLM 多模型客户端（命令行用）
│   └── wait_for_pdf.py           # PDF 等待逻辑
├── notes/                        # 分析输出（.gitignore 中）
│   ├── INDEX.md                  # 全库索引
│   └── 2024/ 2025/ 2026/         # 按年份分类的 Markdown 分析
├── config.yaml                   # 你的配置（含 Key，不提交！）
├── config.example.yaml           # 配置模板（可安全提交）
└── requirements.txt
```

---

## ⚠️ PDF 读取说明

分析时输出面板会明确显示读取状态：

```
✅ 全文读取: 48,271 字符 (100%)
⚠️  文本已截断: 使用前 60,000 字符 (82%)
```

VS Code 扩展模式通过 `vscode.lm` 支持最长 **128k token** 上下文，比命令行模式（30k 字符限制）覆盖更多论文。

---

## 🔧 Read Paper Skill 框架

分析框架定义在 `skills/read-paper/SKILL.md`，可直接修改定制风格：

1. **问题与挑战** — 当前领域问题，作者 Insight
2. **方法设计** — 如何利用 Insight 设计解决方案
3. **实验与贡献** — 实验如何对应贡献，Metrics 表现
4. **本质启发与局限** — 可迁移的启发，方法的根本限制

---

## 📄 License

MIT License © 2025

