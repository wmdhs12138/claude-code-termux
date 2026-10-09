#!/data/data/com.termux/files/usr/bin/bash
# Download URL to OUT with retries, showing one compact progress bar.
#
#   download.sh [--resume] URL OUT [LABEL]
#
# The bar goes to fd $CLAUDE_CODE_TERMUX_PROGRESS_FD when that is set (a
# caller such as `claude update` logs everything else and keeps only progress
# on the terminal), otherwise to stderr; and only when that fd is a terminal.
# Errors from curl are always shown.
set -euo pipefail

resume=()
if [ "${1:-}" = "--resume" ]; then
  resume=(-C -)
  shift
fi
url="${1:?URL required}"
out="${2:?output path required}"
label="${3:-}"
fd="${CLAUDE_CODE_TERMUX_PROGRESS_FD:-2}"
here="$(cd "$(dirname "$0")" && pwd)"

if [ -t "$fd" ]; then
  curl -fL --show-error --progress-bar --retry 3 "${resume[@]}" -o "$out" "$url" 2>&1 \
    | python3 "$here/compact-progress.py" "$label" 2>&"$fd"
else
  curl -fsSL --retry 3 "${resume[@]}" -o "$out" "$url"
fi
