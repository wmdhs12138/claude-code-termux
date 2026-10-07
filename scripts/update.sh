#!/data/data/com.termux/files/usr/bin/bash
# Retired. Early installs put a shell launcher at $PREFIX/bin/claude whose
# `claude update` ran this script; it rebuilt unapproved upstream releases and
# reinstalled that launcher. The direct ELF's embedded updater replaces both.
# This stub stays so those launchers get a migration hint, not a missing file.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "claude update: this shell launcher is retired." >&2
echo "claude update: run 'cd \"$ROOT\" && ./install.sh' once to install the direct ELF;" >&2
echo "claude update: after that, 'claude update' follows Bionic-accepted Releases." >&2
exit 1
