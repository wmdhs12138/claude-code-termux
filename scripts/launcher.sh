#!/data/data/com.termux/files/usr/bin/bash
# claude launcher: latest Claude Code, native bionic on Termux.
# Installed by `make install`; @ROOT@ is replaced with the project path.
set -euo pipefail

# Optional local overrides, kept out of the repo.
# Default path: ~/.config/claude-code/env.sh ; override with $CLAUDE_CODE_TERMUX_ENV.
# Sourced before anything is resolved, so the file can override every variable
# below -- including the launcher's own CLAUDE_CODE_TERMUX_ROOT / _BIN. Sourcing
# it later would make those two silently ineffective when set there.
ENV_FILE="${CLAUDE_CODE_TERMUX_ENV:-$HOME/.config/claude-code/env.sh}"
# The default path may legitimately not exist. An explicitly-set one must, or a
# typo would leave you silently running on a different config than you asked for.
if [ -n "${CLAUDE_CODE_TERMUX_ENV:-}" ] && [ ! -r "$ENV_FILE" ]; then
  echo "claude: CLAUDE_CODE_TERMUX_ENV=$ENV_FILE is not readable" >&2
  exit 1
fi
if [ -r "$ENV_FILE" ]; then
  # The file is user configuration, written in loose shell. Under `set -u` a
  # perfectly ordinary line like `export ANTHROPIC_AUTH_TOKEN="$TOKEN"` (with
  # TOKEN unset) would kill the launcher outright. Relax -u while sourcing it
  # and keep -e, so a genuinely broken file still stops us loudly.
  set +u
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set -u
fi

ROOT="${CLAUDE_CODE_TERMUX_ROOT:-@ROOT@}"

# `claude update` is intercepted: the official self-updater would replace this
# bionic build with a glibc binary. Rebuild from the latest release instead.
case "${1:-}" in
  update|upgrade)
    shift
    exec bash "$ROOT/scripts/update.sh" "$@"
    ;;
esac

BIN="${CLAUDE_CODE_TERMUX_BIN:-$ROOT/dist/claude}"
if [ ! -x "$BIN" ]; then
  echo "claude: binary not found at $BIN (run 'make build' in $ROOT)" >&2
  exit 1
fi

# Required on Termux: the embedded ripgrep is a linux/glibc binary; use system rg.
export USE_BUILTIN_RIPGREP=0
# Official self-update would fetch a glibc build and break the grafted runtime.
# (`claude update` is intercepted above and rebuilds locally instead.)
export DISABLE_AUTOUPDATER=1

exec "$BIN" "$@"
