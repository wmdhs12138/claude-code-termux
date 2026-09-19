#!/usr/bin/env bash
# One-command local installer for the direct-exec Bionic Claude ELF.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="latest"
BUILD=1

usage() {
  cat <<'EOF'
Usage: ./install.sh [VERSION]

Build and atomically install the native Bionic AArch64 Claude Code ELF.

Options:
  --no-build   Install an existing dist/claude (used by make install)
  -h, --help   Show this help

Environment:
  CLAUDE_CODE_TERMUX_INSTALL_DIR       destination directory (default: ~/bin)
  CLAUDE_CODE_TERMUX_SKIP_DEPS=1       do not install missing Termux packages
  CLAUDE_CODE_TERMUX_ALLOW_UNSUPPORTED=1
                                       bypass Termux/AArch64/API checks
EOF
}

version_seen=0
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "install: unknown option: $arg" >&2; usage >&2; exit 2 ;;
    *)
      if [ "$version_seen" = "1" ]; then
        echo "install: only one version may be specified" >&2
        exit 2
      fi
      VERSION="$arg"
      version_seen=1
      ;;
  esac
done

if [ "${CLAUDE_CODE_TERMUX_ALLOW_UNSUPPORTED:-0}" != "1" ]; then
  if [ "$(uname -m)" != "aarch64" ]; then
    echo "install: this port requires an AArch64 device (found $(uname -m))" >&2
    exit 1
  fi
  case "${PREFIX:-}" in
    */com.termux/files/usr) ;;
    *)
      echo "install: run this installer inside Termux" >&2
      exit 1
      ;;
  esac
  if [ -x /system/bin/getprop ]; then
    sdk="$(/system/bin/getprop ro.build.version.sdk 2>/dev/null || true)"
    case "$sdk" in
      ''|*[!0-9]*) ;;
      *)
        if [ "$sdk" -lt 28 ]; then
          echo "install: Android API $sdk is unsupported; API 28+ is required" >&2
          exit 1
        fi
        ;;
    esac
  fi
fi

if [ "$BUILD" = "1" ]; then
  commands=(bash curl git python3 unzip rg flock sha256sum tar awk sed mktemp)
  packages=(bash curl git python unzip ripgrep util-linux coreutils tar gawk sed coreutils)
  missing_packages=()

  for i in "${!commands[@]}"; do
    if ! command -v "${commands[$i]}" >/dev/null 2>&1; then
      package="${packages[$i]}"
      duplicate=0
      for queued in "${missing_packages[@]:-}"; do
        if [ "$queued" = "$package" ]; then duplicate=1; break; fi
      done
      if [ "$duplicate" = "0" ]; then missing_packages+=("$package"); fi
    fi
  done

  if [ "${#missing_packages[@]}" -gt 0 ]; then
    if [ "${CLAUDE_CODE_TERMUX_SKIP_DEPS:-0}" = "1" ]; then
      echo "install: missing packages: ${missing_packages[*]}" >&2
      exit 1
    fi
    if ! command -v pkg >/dev/null 2>&1; then
      echo "install: missing packages and the Termux pkg command is unavailable" >&2
      exit 1
    fi
    echo "install: installing missing packages: ${missing_packages[*]}" >&2
    pkg install -y "${missing_packages[@]}"
    hash -r
  fi

  for command_name in "${commands[@]}"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      echo "install: required command is still missing: $command_name" >&2
      exit 1
    fi
  done

  echo "install: building Claude Code $VERSION" >&2
  bash "$ROOT/scripts/build.sh" "$VERSION"
fi

CANDIDATE="$ROOT/dist/claude"
if [ ! -x "$CANDIDATE" ]; then
  echo "install: $CANDIDATE is missing or not executable; run ./install.sh first" >&2
  exit 1
fi

candidate_version="$($CANDIDATE --version 2>/dev/null | awk 'NR == 1 {print $1}')"
if [[ ! "$candidate_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "install: candidate version probe failed" >&2
  exit 1
fi

INSTALL_DIR="${CLAUDE_CODE_TERMUX_INSTALL_DIR:-$HOME/bin}"
TARGET="$INSTALL_DIR/claude"
mkdir -p "$INSTALL_DIR"

backup=""
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="$TARGET.backup-$stamp"
  suffix=0
  while [ -e "$backup" ] || [ -L "$backup" ]; do
    suffix=$((suffix + 1))
    backup="$TARGET.backup-$stamp.$suffix"
  done
  cp -pP "$TARGET" "$backup"
fi

replacement="$(mktemp "$INSTALL_DIR/.claude-install.XXXXXX")"
trap 'rm -f "$replacement"' EXIT
cp "$CANDIDATE" "$replacement"
chmod 700 "$replacement"
replacement_version="$($replacement --version 2>/dev/null | awk 'NR == 1 {print $1}')"
if [ "$replacement_version" != "$candidate_version" ]; then
  echo "install: copied candidate failed its version probe" >&2
  exit 1
fi
mv -f "$replacement" "$TARGET"
trap - EXIT

echo "installed: $TARGET ($replacement_version)"
if [ -n "$backup" ]; then echo "backup:    $backup"; fi
case ":${PATH:-}:" in
  *":$INSTALL_DIR:"*) ;;
  *) echo "PATH:      add 'export PATH=\"$INSTALL_DIR:\$PATH\"' to your shell profile" ;;
esac
