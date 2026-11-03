#!/usr/bin/env bash
set -euo pipefail

LABEL="${1:-herdr-master}"
CWD="${2:-$PWD}"

echo "Creating new Herdr workspace: $LABEL at $CWD"
WS_JSON=$(herdr workspace create --cwd "$CWD" --label "$LABEL" --focus)
WS_ID=$(echo "$WS_JSON" | grep -o '"workspace_id":"[^"]*"' | head -n 1 | cut -d'"' -f4)

echo "Opening Herdr-Master Cockpit tab in workspace $WS_ID..."
herdr plugin pane open --plugin devswha.herdr-master --entrypoint cockpit --workspace "$WS_ID" --placement tab --focus
