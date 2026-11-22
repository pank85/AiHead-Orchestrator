#!/usr/bin/env bash
# Display an inspection tab and relay one response without an LLM session.
set -euo pipefail
FILE=""
TO=""
TAG=""
INFO=0
ANSWER=""
SESSION="${SESSION:-default}"
BACKEND="${HC_TERMINAL_ID:+core}"
BACKEND="${BACKEND:-herdr}"
TAB_ID=""
TYPE=""
usage() {
  printf '%s\n' "Usage: $0 --file <path.md> --to <pane> --tag <tag> [--info] [--answer <text>] [--backend core|herdr] [--session <session>] [--tab-id <id> --type Plan|Update|Info|Q]"
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --file|-f|--to|-t|--tag|-g|--answer|-a|--backend|--session|--tab-id|--type)
      if [[ $# -lt 2 || -z "$2" ]]; then echo "Missing value for $1" >&2; exit 2; fi
      case "$1" in
        --file|-f) FILE="$2" ;;
        --to|-t) TO="$2" ;;
        --tag|-g) TAG="$2" ;;
        --answer|-a) ANSWER="$2" ;;
        --backend) BACKEND="$2" ;;
        --session) SESSION="$2" ;;
        --tab-id) TAB_ID="$2" ;;
        --type) TYPE="$2" ;;
      esac
      shift 2 ;;
    --info|-i) INFO=1; shift ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ -z "$FILE" || -z "$TO" ]]; then usage >&2; exit 2; fi
case "$BACKEND" in core|herdr) ;; *) echo "Invalid backend: $BACKEND" >&2; exit 2 ;; esac
if [[ -n "$TYPE" ]]; then
  case "$TYPE" in Plan|Update|Info|Q) ;; *) echo "Invalid type: $TYPE" >&2; exit 2 ;; esac
  if [[ -z "$TAB_ID" || ( "$TYPE" == Q && -z "$TAG" ) ]]; then
    echo "--type requires --tab-id (and --tag for Q)" >&2; exit 2
  fi
  if [[ "$TYPE" != Q ]]; then INFO=1; fi
elif [[ -z "$TAG" || -n "$TAB_ID" ]]; then
  echo "Provide --tag, or --type with --tab-id" >&2; exit 2
fi
cat "$FILE"
echo ""
if [[ -z "$ANSWER" ]]; then
  if [[ "$INFO" -eq 1 ]]; then HINT="Enter = οκ, ή γράψε ερώτηση: "; else HINT="Η απάντησή σου (μία γραμμή): "; fi
  while [[ -z "$ANSWER" ]]; do
    if ! IFS= read -r -p "$HINT" ANSWER; then
      echo "Ακυρώθηκε (EOF)." >&2; exit 1
    fi
    if [[ "$INFO" -eq 1 && -z "$ANSWER" ]]; then ANSWER="οκ"; fi
  done
fi
if [[ "$TYPE" == Q ]]; then
  PAYLOAD="User answer for ${TAG} tab ${TAB_ID}: ${ANSWER}"
elif [[ -n "$TYPE" ]]; then
  PAYLOAD="User ack/reply for ${TYPE} tab ${TAB_ID}: ${ANSWER}"
else
  PAYLOAD="${TAG}: ${ANSWER}"
fi
ec=0
if [[ "$BACKEND" == core ]]; then
  hc send "$TO" "$PAYLOAD" >/dev/null || ec=$?
else
  herdr --session "$SESSION" agent prompt "$TO" "$PAYLOAD" >/dev/null || ec=$?
fi
if [[ "$ec" -ne 0 ]]; then
  printf '\033[31mΑΠΟΤΥΧΙΑ αποστολής (%s exit %s). Γράψε την απάντηση στον master.\033[0m\n' "$BACKEND" "$ec" >&2
  IFS= read -r -p "Enter για κλείσιμο" _ || true
  exit 1
fi
printf '\033[32mστάλθηκε → %s\033[0m\n' "$TO"
