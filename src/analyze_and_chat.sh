#!/usr/bin/env bash
# analyze_and_chat.sh — 在弹出终端内运行：分析论文 → 可选追问
# 用法: analyze_and_chat.sh ITEM_KEY [MODEL]

set -euo pipefail

ITEM_KEY="${1:-}"
MODEL="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "$ITEM_KEY" ]]; then
  echo "❌ 未提供 Item Key"
  read -rp "按 Enter 关闭..."
  exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         🧠  Zotero Paper AI Manager — 自动分析              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "📌 Zotero Item Key: $ITEM_KEY"
echo "$(date '+%Y-%m-%d %H:%M:%S') 开始分析..."
echo ""

# ── 运行分析 ─────────────────────────────────────────────────
if [[ -n "$MODEL" ]]; then
  python3 "$SCRIPT_DIR/paper_analyzer.py" --key "$ITEM_KEY" --model "$MODEL"
else
  python3 "$SCRIPT_DIR/paper_analyzer.py" --key "$ITEM_KEY"
fi

ANALYZE_EXIT=$?

echo ""
if [[ $ANALYZE_EXIT -ne 0 ]]; then
  echo "⚠️  分析过程出现错误（退出码: $ANALYZE_EXIT）"
fi

# ── 提示进入追问模式 ──────────────────────────────────────────
echo "══════════════════════════════════════════════════════════════"
echo ""
read -rp "💬 是否对这篇论文进行追问对话？[Y/n] " CHAT_ANS
CHAT_ANS="${CHAT_ANS:-y}"

if [[ "${CHAT_ANS,,}" == "y" ]]; then
  echo ""
  if [[ -n "$MODEL" ]]; then
    python3 "$SCRIPT_DIR/paper_chat.py" --key "$ITEM_KEY" --model "$MODEL" --no-pdf
  else
    python3 "$SCRIPT_DIR/paper_chat.py" --key "$ITEM_KEY" --no-pdf
  fi
fi

echo ""
echo "✅ 完成。此终端保持打开，可继续手动操作（输入 exit 关闭）。"
echo "   快速追问命令："
echo "   python3 $SCRIPT_DIR/paper_chat.py --key $ITEM_KEY"
echo ""
# 保持终端为交互 shell，用户可继续输入命令
cd "$SCRIPT_DIR"
exec bash
