#!/data/data/com.termux/files/usr/bin/bash
# Download the official Claude Code linux-arm64 binary and verify its sha256.
# Usage: fetch-claude.sh [VERSION|latest] [OUTDIR]
# Prints the output path; writes the resolved version to OUTDIR/.claude-version
set -euo pipefail

VERSION="${1:-latest}"
OUTDIR="${2:-$(cd "$(dirname "$0")/.." && pwd)/work}"
BASE="https://downloads.claude.ai/claude-code-releases"

mkdir -p "$OUTDIR"

if [ "$VERSION" = "latest" ]; then
  VERSION="$(curl -fsSL --max-time 30 "$BASE/latest")"
fi
case "$VERSION" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) echo "fetch-claude: invalid version '$VERSION'" >&2; exit 1 ;;
esac

MANIFEST="$(curl -fsSL --max-time 30 "$BASE/$VERSION/manifest.json")"
read -r CHECKSUM SIZE < <(printf '%s' "$MANIFEST" | python3 -c '
import json, sys
p = json.load(sys.stdin)["platforms"]["linux-arm64"]
print(p["checksum"], p["size"])
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
