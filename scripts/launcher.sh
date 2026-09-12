#!/data/data/com.termux/files/usr/bin/bash
# claude launcher: latest Claude Code, native bionic on Termux.
# Installed by `make install`; @ROOT@ is replaced with the project path.
set -euo pipefail

ROOT="${CLAUDE_TERMUX_ROOT:-@ROOT@}"
BIN="${CLAUDE_TERMUX_BIN:-$ROOT/dist/claude}"
if [ ! -x "$BIN" ]; then
  echo "claude: binary not found at $BIN (run 'make build' in $ROOT)" >&2
  exit 1
fi

# Required on Termux: the embedded ripgrep is a linux/glibc binary; use system rg.
export USE_BUILTIN_RIPGREP=0
# Native auto-update would fetch a glibc build and break the grafted runtime.
export DISABLE_AUTOUPDATER=1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
# Silence the model-catalog warning for non-Anthropic model ids.
export CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1

# DeepSeek Anthropic-compatible endpoint defaults (used only when no
# Anthropic credentials are already present in the environment).
KEY_FILE="${CLAUDE_DS_KEY_FILE:-$HOME/.config/claude-code/deepseek.key}"
if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -r "$KEY_FILE" ]; then
  export ANTHROPIC_AUTH_TOKEN="$(tr -d '[:space:]' < "$KEY_FILE")"
  export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://api.deepseek.com/anthropic}"
  export ANTHROPIC_MODEL="${CLAUDE_DS_MODEL:-deepseek-flash}"
  export ANTHROPIC_SMALL_FAST_MODEL="deepseek-flash"
  export ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek-flash"
  export ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-flash"
  export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek-v4-pro"
fi

exec "$BIN" "$@"
