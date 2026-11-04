#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HM_CANONICAL="$REPO_ROOT/skills/herdr-master/SKILL.md"
ERGO_CANONICAL="$REPO_ROOT/skills/ergo/SKILL.md"

if [ ! -f "$HM_CANONICAL" ]; then
    echo "Error: Canonical herdr-master skill not found at $HM_CANONICAL"
    exit 1
fi

if [ ! -f "$ERGO_CANONICAL" ]; then
    echo "Error: Canonical ergo skill not found at $ERGO_CANONICAL"
    exit 1
fi

echo "================================================================="
echo "👑 Installing Herdr-Master & Ergo Skills Across Agent Harnesses"
echo "================================================================="

# 1. Antigravity (agy)
AGY_SKILLS="$HOME/.gemini/config/skills"
mkdir -p "$AGY_SKILLS/herdr-master" "$AGY_SKILLS/ergo"
ln -sf "$HM_CANONICAL" "$AGY_SKILLS/herdr-master/SKILL.md"
ln -sf "$ERGO_CANONICAL" "$AGY_SKILLS/ergo/SKILL.md"
echo "[✓] Antigravity (agy):"
echo "    - /herdr-master -> $AGY_SKILLS/herdr-master/SKILL.md"
echo "    - /ergo         -> $AGY_SKILLS/ergo/SKILL.md"

# 2. Pi (pi)
PI_PROMPTS="$HOME/.pi/agent/prompts"
mkdir -p "$PI_PROMPTS"
ln -sf "$HM_CANONICAL" "$PI_PROMPTS/herdr-master.md"
ln -sf "$ERGO_CANONICAL" "$PI_PROMPTS/ergo.md"
echo "[✓] Pi (pi):"
echo "    - /herdr-master -> $PI_PROMPTS/herdr-master.md"
echo "    - /ergo         -> $PI_PROMPTS/ergo.md"

# 3. Claude Code (claude)
CLAUDE_CMDS="$HOME/.claude/commands"
mkdir -p "$CLAUDE_CMDS"
ln -sf "$HM_CANONICAL" "$CLAUDE_CMDS/herdr-master.md"
ln -sf "$ERGO_CANONICAL" "$CLAUDE_CMDS/ergo.md"
echo "[✓] Claude Code (claude):"
echo "    - /herdr-master -> $CLAUDE_CMDS/herdr-master.md"
echo "    - /ergo         -> $CLAUDE_CMDS/ergo.md"

# 4. OpenCode (opencode)
OPENCODE_SKILLS="$HOME/.config/opencode/skills"
OPENCODE_CMDS="$HOME/.config/opencode/commands"
mkdir -p "$OPENCODE_SKILLS/herdr-master" "$OPENCODE_SKILLS/ergo" "$OPENCODE_CMDS"
ln -sf "$HM_CANONICAL" "$OPENCODE_SKILLS/herdr-master/SKILL.md"
ln -sf "$ERGO_CANONICAL" "$OPENCODE_SKILLS/ergo/SKILL.md"
ln -sf "$HM_CANONICAL" "$OPENCODE_CMDS/herdr-master.md"
ln -sf "$ERGO_CANONICAL" "$OPENCODE_CMDS/ergo.md"
echo "[✓] OpenCode (opencode):"
echo "    - /herdr-master -> $OPENCODE_SKILLS/herdr-master/SKILL.md & $OPENCODE_CMDS/herdr-master.md"
echo "    - /ergo         -> $OPENCODE_SKILLS/ergo/SKILL.md & $OPENCODE_CMDS/ergo.md"

echo "================================================================="
echo "✨ Success! Skills & slash commands are installed in all 4 harnesses:"
echo "   • /herdr-master  [task]"
echo "   • /ergo          [--herdr] [--models ...] [intent]"
echo "================================================================="
