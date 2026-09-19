#!/data/data/com.termux/files/usr/bin/bash
# Materialize one immutable, content-addressed Android Bun base for all
# self-update toolchains. Prints the verified Bun path on stdout.
set -euo pipefail

CACHE_ROOT="${1:?shared cache directory required}"
BUN_URL="${2:?Bun URL required}"
ARCHIVE_SHA="${3:?archive sha256 required}"
BINARY_SHA="${4:?binary sha256 required}"
PROGRESS_HELPER="${5:?progress helper required}"

for pair in "archive:$ARCHIVE_SHA" "binary:$BINARY_SHA"; do
  name="${pair%%:*}"
  value="${pair#*:}"
  if ! [[ "$value" =~ ^[0-9a-f]{64}$ ]]; then
    echo "bun-base: invalid $name sha256" >&2
    exit 2
  fi
done

mkdir -p "$CACHE_ROOT"
exec 8>"$CACHE_ROOT/.lock"
flock 8

TARGET_DIR="$CACHE_ROOT/bun-$BINARY_SHA"
TARGET="$TARGET_DIR/bun"
if [ -x "$TARGET" ] && [ "$(sha256sum "$TARGET" | cut -d' ' -f1)" = "$BINARY_SHA" ]; then
  echo "build: using shared Android Bun base ${BINARY_SHA:0:12} (cached)" >&2
  echo "$TARGET"
  exit 0
fi

# A corrupt/incomplete directory never becomes a seed. The exact target is
# content-addressed under CACHE_ROOT; unrelated cache entries are untouched.
rm -rf "$TARGET_DIR"
STAGE="$(mktemp -d "$CACHE_ROOT/.bun-$BINARY_SHA.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
mkdir "$STAGE/base"

# First run after upgrading can promote an already verified per-version Bun,
# avoiding one final network download while old cache layouts age out.
shopt -s nullglob
for seed in \
  "$CACHE_ROOT"/../claude-*/work/bun-android/bun \
  "$CACHE_ROOT"/../toolchain-*/work/bun-android/bun; do
  if [ -x "$seed" ] && [ "$(sha256sum "$seed" | cut -d' ' -f1)" = "$BINARY_SHA" ]; then
    cp -p "$seed" "$STAGE/base/bun"
    chmod 555 "$STAGE/base/bun"
    printf '%s\n' "$ARCHIVE_SHA" > "$STAGE/base/archive.sha256"
    mv "$STAGE/base" "$TARGET_DIR"
    trap - EXIT
    rm -rf "$STAGE"
    echo "build: using shared Android Bun base ${BINARY_SHA:0:12} (migrated)" >&2
    echo "$TARGET"
    exit 0
  fi
done

echo "build: downloading new Android Bun base ${BINARY_SHA:0:12}..." >&2
ARCHIVE="$STAGE/bun.zip"
if [ -t 2 ]; then
  curl -fL --show-error --progress-bar --retry 3 -o "$ARCHIVE" "$BUN_URL" \
    2>&1 | python3 "$PROGRESS_HELPER"
else
  curl -fsSL --retry 3 -o "$ARCHIVE" "$BUN_URL"
fi
ACTUAL_ARCHIVE_SHA="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
if [ "$ACTUAL_ARCHIVE_SHA" != "$ARCHIVE_SHA" ]; then
  echo "bun-base: archive checksum mismatch (got $ACTUAL_ARCHIVE_SHA)" >&2
  exit 1
fi
unzip -o -j "$ARCHIVE" "*/bun" -d "$STAGE/base" >/dev/null
chmod 555 "$STAGE/base/bun"
ACTUAL_BINARY_SHA="$(sha256sum "$STAGE/base/bun" | cut -d' ' -f1)"
if [ "$ACTUAL_BINARY_SHA" != "$BINARY_SHA" ]; then
  echo "bun-base: binary checksum mismatch (got $ACTUAL_BINARY_SHA)" >&2
  exit 1
fi
printf '%s\n' "$ARCHIVE_SHA" > "$STAGE/base/archive.sha256"
mv "$STAGE/base" "$TARGET_DIR"
trap - EXIT
rm -rf "$STAGE"
echo "build: Android Bun download verified; shared base ready" >&2
echo "$TARGET"
