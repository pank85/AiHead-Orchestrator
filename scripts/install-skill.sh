#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_CANONICAL="$REPO_ROOT/skills/herdr-master/SKILL.md"

if [ ! -f "$SKILL_CANONICAL" ]; then
    echo "Error: Canonical skill file not found at $SKILL_CANONICAL"
    exit 1
fi

echo "=== Installing Herdr-Master Skill across Agent Harnesses ==="

# 1. Antigravity (agy)
AGY_DIR="$HOME/.gemini/config/skills/herdr-master"
mkdir -p "$AGY_DIR"
ln -sf "$SKILL_CANONICAL" "$AGY_DIR/SKILL.md"
echo "[✓] Antigravity: linked to $AGY_DIR/SKILL.md"

# 2. Pi (pi)
PI_DIR="$HOME/.pi/agent/prompts"
mkdir -p "$PI_DIR"
ln -sf "$SKILL_CANONICAL" "$PI_DIR/herdr-master.md"
echo "[✓] Pi: linked to $PI_DIR/herdr-master.md"

# 3. Claude Code (claude)
CLAUDE_DIR="$HOME/.claude/commands"
mkdir -p "$CLAUDE_DIR"
ln -sf "$SKILL_CANONICAL" "$CLAUDE_DIR/herdr-master.md"
echo "[✓] Claude Code: linked to $CLAUDE_DIR/herdr-master.md"

# 4. OpenCode (opencode)
OPENCODE_SKILL_DIR="$HOME/.config/opencode/skills/herdr-master"
OPENCODE_CMD_DIR="$HOME/.config/opencode/commands"
mkdir -p "$OPENCODE_SKILL_DIR" "$OPENCODE_CMD_DIR"
ln -sf "$SKILL_CANONICAL" "$OPENCODE_SKILL_DIR/SKILL.md"
ln -sf "$SKILL_CANONICAL" "$OPENCODE_CMD_DIR/herdr-master.md"
echo "[✓] OpenCode: linked to $OPENCODE_SKILL_DIR and $OPENCODE_CMD_DIR"

echo "=== Done! Herdr-Master skill is active in all 4 harnesses. ==="
