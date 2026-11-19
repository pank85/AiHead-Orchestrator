#!/usr/bin/env bash
# herdr-ask.sh — tab ❓/ℹ️ χωρίς LLM session (0 tokens).
# Δείχνει ένα .md, παίρνει την απάντηση του χρήστη και τη στέλνει ΑΥΤΟΥΣΙΑ στον master με `hc send`.
# Χρήση: herdr-ask.sh --file <path.md> --to <tN του master> --tag <approval-<code>> [--info] [--answer <κείμενο, για test>]
set -euo pipefail

FILE=""
TO=""
TAG=""
INFO=0
ANSWER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file|-f)   FILE="${2:-}"; shift 2 ;;
    --to|-t)     TO="${2:-}"; shift 2 ;;
    --tag|-g)    TAG="${2:-}"; shift 2 ;;
    --info|-i)   INFO=1; shift ;;
    --answer|-a) ANSWER="${2:-}"; shift 2 ;;
    *)
      echo "Άγνωστη παράμετρος: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$FILE" || -z "$TO" || -z "$TAG" ]]; then
  echo "Χρήση: $0 --file <path.md> --to <tN> --tag <approval-code> [--info] [--answer <text>]" >&2
  exit 2
fi

cat "$FILE"
echo ""

if [[ -z "$ANSWER" ]]; then
  if [[ "$INFO" -eq 1 ]]; then
    HINT="Enter = οκ, ή γράψε ερώτηση: "
  else
    HINT="Η απάντησή σου (μία γραμμή): "
  fi
  while [[ -z "$ANSWER" ]]; do
    if ! IFS= read -r -p "$HINT" ANSWER; then
      echo "" >&2
      echo "Ακυρώθηκε (EOF)." >&2
      exit 1
    fi
    if [[ "$INFO" -eq 1 && -z "$ANSWER" ]]; then
      ANSWER="οκ"
    fi
  done
fi

# Μία γραμμή απάντησης· για πολύγραμμες απαντήσεις γράψε στον master απευθείας.
ec=0
hc send "$TO" "${TAG}: ${ANSWER}" >/dev/null || ec=$?
if [[ "$ec" -ne 0 ]]; then
  printf '\033[31mΑΠΟΤΥΧΙΑ αποστολής (hc exit %s). Γράψε την απάντηση στον master.\033[0m\n' "$ec"
  IFS= read -r -p "Enter για κλείσιμο" _ || true
  exit 1
fi

printf '\033[32mστάλθηκε → %s\033[0m\n' "$TO"
