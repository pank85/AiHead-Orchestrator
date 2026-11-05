#!/usr/bin/env bash
# Compatibility shim. Workspace creation lives in `herdm workspace`.
set -euo pipefail

label="${1:-herdr-master}"
if [[ $# -ge 2 ]]; then
  cwd="$2"
else
  cwd="$PWD"
fi
if [[ "$cwd" != /* ]]; then
  cwd="$(cd -- "$cwd" && pwd)"
fi
exec herdm workspace --label "$label" --cwd "$cwd"
