#!/data/data/com.termux/files/usr/bin/bash
# claude update: rebuild the native Termux port from the latest official
# Claude Code release. Invoked by the launcher (`claude update ...`).
#
# Usage:
#   claude update            # check + rebuild if a newer version exists
#   claude update --check    # check only, exit 0 if up to date, 1 otherwise
#   claude update --force    # rebuild even if already up to date
set -euo pipefail

ROOT="${CLAUDE_CODE_TERMUX_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
BIN="${CLAUDE_CODE_TERMUX_BIN:-$ROOT/dist/claude}"
LATEST_URL="https://downloads.claude.ai/claude-code-releases/latest"

CHECK_ONLY=0
FORCE=0
for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    --force|-f) FORCE=1 ;;
    -h|--help)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "claude update: unknown option '$a' (try --check/--force)" >&2; exit 2 ;;
  esac
done

LATEST="$(curl -fsSL --max-time 30 "$LATEST_URL")"
case "$LATEST" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) echo "claude update: bad latest version '$LATEST'" >&2; exit 1 ;;
esac

CURRENT=""
if [ -x "$BIN" ]; then
  CURRENT="$("$BIN" --version 2>/dev/null | awk '{print $1}')"
fi
echo "current: ${CURRENT:-none}"
echo "latest:  $LATEST"

if [ "$CURRENT" = "$LATEST" ] && [ "$FORCE" != "1" ]; then
  echo "claude is up to date"
  exit 0
fi
if [ "$CHECK_ONLY" = "1" ]; then
  echo "update available"
  exit 1
fi

echo "building $LATEST (downloads ~250 MB, takes a few minutes)..."
SKIP_RUN="${SKIP_RUN:-0}" bash "$ROOT/scripts/build.sh" "$LATEST"

# refresh the installed launcher in case the template changed
mkdir -p "$HOME/bin"
# Escape the path before putting it through sed: an '&' or '|' in it would
# otherwise silently produce a launcher pointing somewhere else.
ROOT_ESC="$(printf '%s' "$ROOT" | sed 's/[&|\\]/\\&/g')"
TMP="$HOME/bin/.claude.new.$$"
sed "s|@ROOT@|$ROOT_ESC|g" "$ROOT/scripts/launcher.sh" > "$TMP"
chmod +x "$TMP"
mv -f "$TMP" "$HOME/bin/claude"

echo "updated: $("$BIN" --version)"
