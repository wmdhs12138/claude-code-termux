#!/data/data/com.termux/files/usr/bin/bash
# Download the official Claude Code linux-arm64 binary and verify its sha256.
# Usage: fetch-claude.sh [VERSION|latest] [OUTDIR]
# Prints the output path; writes the resolved version to OUTDIR/.claude-version
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-latest}"
OUTDIR="${2:-$ROOT/work}"
BASE="https://downloads.claude.ai/claude-code-releases"

# Standalone fetches share the build lock because work/.claude-version and the
# downloaded binary are consumed as one pair by build.sh.
if [ "${CLAUDE_CODE_TERMUX_LOCK_HELD:-0}" != "1" ]; then
  exec 9>"$ROOT/.build.lock"
  if ! flock -n 9; then
    echo "fetch-claude: another build or fetch is already running" >&2
    exit 1
  fi
fi

mkdir -p "$OUTDIR"

if [ "$VERSION" = "latest" ]; then
  VERSION="$(curl -fsSL --max-time 30 "$BASE/latest")"
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "fetch-claude: invalid version '$VERSION'" >&2
  exit 1
fi

MANIFEST="$(curl -fsSL --max-time 30 "$BASE/$VERSION/manifest.json")"
read -r CHECKSUM SIZE < <(printf '%s' "$MANIFEST" | python3 -c '
import json, re, sys
p = json.load(sys.stdin)["platforms"]["linux-arm64"]
checksum, size = p["checksum"], p["size"]
if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
    raise SystemExit("manifest has an invalid linux-arm64 checksum")
if type(size) is not int or size <= 0:
    raise SystemExit("manifest has an invalid linux-arm64 size")
print(checksum.lower(), size)
')

OUT="$OUTDIR/claude-$VERSION-linux-arm64"
if [ -f "$OUT" ] && [ "$(stat -c%s "$OUT")" = "$SIZE" ] \
   && [ "$(sha256sum "$OUT" | cut -d' ' -f1)" = "$CHECKSUM" ]; then
  echo "fetch-claude: cached $OUT" >&2
else
  echo "fetch-claude: downloading $VERSION ($SIZE bytes)..." >&2
  curl -fL --retry 3 -C - -o "$OUT" "$BASE/$VERSION/linux-arm64/claude"
  ACTUAL="$(sha256sum "$OUT" | cut -d' ' -f1)"
  if [ "$ACTUAL" != "$CHECKSUM" ]; then
    echo "fetch-claude: checksum mismatch (got $ACTUAL)" >&2
    rm -f "$OUT"
    exit 1
  fi
fi

printf '%s' "$VERSION" > "$OUTDIR/.claude-version"
echo "$OUT"
