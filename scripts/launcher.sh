#!/data/data/com.termux/files/usr/bin/bash
# claude launcher: latest Claude Code, native bionic on Termux.
# Installed by `make install`; @ROOT@ is replaced with the project path.
set -euo pipefail

ROOT="${CLAUDE_TERMUX_ROOT:-@ROOT@}"

# `claude update` is intercepted: the official self-updater would replace this
# bionic build with a glibc binary. Rebuild from the latest release instead.
case "${1:-}" in
  update|upgrade)
    shift
    exec bash "$ROOT/scripts/update.sh" "$@"
    ;;
esac

BIN="${CLAUDE_TERMUX_BIN:-$ROOT/dist/claude}"
if [ ! -x "$BIN" ]; then
  echo "claude: binary not found at $BIN (run 'make build' in $ROOT)" >&2
  exit 1
fi

# Required on Termux: the embedded ripgrep is a linux/glibc binary; use system rg.
export USE_BUILTIN_RIPGREP=0
# Official self-update would fetch a glibc build and break the grafted runtime.
# (`claude update` is intercepted above and rebuilds locally instead.)
export DISABLE_AUTOUPDATER=1

# Account, model and endpoint settings are intentionally untouched: use
# Claude Code's official defaults, or export ANTHROPIC_* yourself.

exec "$BIN" "$@"
