#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if command -v uv >/dev/null 2>&1; then
    exec uv run --project "$REPO_ROOT" herdm skills install "$@"
fi

if [ -n "${VIRTUAL_ENV:-}" ] && command -v herdm >/dev/null 2>&1; then
    exec herdm skills install "$@"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c "import herdr_master" 2>/dev/null; then
    exec python3 -m herdr_master.cli.main skills install "$@"
fi

echo "Error: 'uv' or active virtualenv is required to install skills from local checkout $REPO_ROOT." >&2
echo "Please install uv (https://docs.astral.sh/uv/) or activate a virtual environment with herdr-master installed." >&2
exit 1
