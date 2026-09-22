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
PROBE_TIMEOUT="${CLAUDE_CODE_TERMUX_PROBE_TIMEOUT:-10}"
if ! [[ "$PROBE_TIMEOUT" =~ ^[0-9]+([.][0-9]+)?$ ]] || [ "$PROBE_TIMEOUT" = "0" ]; then
  echo "claude update: invalid probe timeout '$PROBE_TIMEOUT'" >&2
  exit 2
fi

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

probe_binary() {
  python3 - "$1" "$PROBE_TIMEOUT" <<'PY'
import os, signal, subprocess, sys
p = subprocess.Popen(
    [sys.argv[1], "--version"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    start_new_session=True,
)
try:
    out, _ = p.communicate(timeout=float(sys.argv[2]))
except subprocess.TimeoutExpired:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.communicate()
    raise SystemExit(124)
if p.returncode:
    raise SystemExit(1)
print(out, end="")
PY
}

LATEST="$(curl -fsSL --max-time 30 "$LATEST_URL")"
if ! [[ "$LATEST" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "claude update: bad latest version '$LATEST'" >&2
  exit 1
fi

CURRENT=""
if [ -x "$BIN" ]; then
  # A broken current binary is exactly when update must remain usable. Treat a
  # crash/non-zero exit as "not installed" instead of letting pipefail abort.
  if VERSION_OUTPUT="$(probe_binary "$BIN" 2>/dev/null)"; then
    CURRENT="$(printf '%s\n' "$VERSION_OUTPUT" | awk 'NR == 1 {print $1}')"
  else
    echo "claude update: current binary is not runnable; rebuilding" >&2
    CURRENT=""
  fi
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

# Refresh the installed launcher in case the template changed. This legacy
# path now follows the same Termux-native default as install.sh.
INSTALL_DIR="${CLAUDE_CODE_TERMUX_INSTALL_DIR:-${PREFIX:-$HOME}/bin}"
mkdir -p "$INSTALL_DIR"
# Escape the path before putting it through sed: an '&' or '|' in it would
# otherwise silently produce a launcher pointing somewhere else.
ROOT_ESC="$(printf '%s' "$ROOT" | sed 's/[&|\\]/\\&/g')"
TMP="$INSTALL_DIR/.claude.new.$$"
sed "s|@ROOT@|$ROOT_ESC|g" "$ROOT/scripts/launcher.sh" > "$TMP"
chmod +x "$TMP"
mv -f "$TMP" "$INSTALL_DIR/claude"

if ! FINAL_VERSION="$(probe_binary "$BIN" 2>/dev/null)"; then
  echo "claude update: rebuilt binary failed its version probe" >&2
  exit 1
fi
echo "updated: $FINAL_VERSION"
