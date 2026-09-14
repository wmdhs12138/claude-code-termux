#!/data/data/com.termux/files/usr/bin/bash
# Build claude: graft Claude Code's standalone module graph onto an
# Android (bionic) Bun ELF. Zero glibc, no ptrace, no proot.
#
# Usage: build.sh [VERSION|latest]
# Env:   BUN_URL  override the Android Bun base (default: Bun canary aarch64-android)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-latest}"
BUN_URL="${BUN_URL:-https://github.com/oven-sh/bun/releases/download/canary/bun-linux-aarch64-android.zip}"
WORK="$ROOT/work"
DIST="$ROOT/dist"
mkdir -p "$WORK" "$DIST"

# 1. official Claude Code linux-arm64 binary (checksum-verified)
CLAUDE_BIN="$(bash "$ROOT/scripts/fetch-claude.sh" "$VERSION" "$WORK")"
VER="$(cat "$WORK/.claude-version")"

# 2. Android Bun base (bionic ELF)
BUN="$WORK/bun-android/bun"
if [ ! -x "$BUN" ]; then
  echo "build: fetching Android Bun base..." >&2
  curl -fL --retry 3 -o "$WORK/bun-android.zip" "$BUN_URL"
  rm -rf "$WORK/bun-android" "$WORK/bun-android.zip.d"
  mkdir -p "$WORK/bun-android"
  unzip -o -j "$WORK/bun-android.zip" "*/bun" -d "$WORK/bun-android" >/dev/null
  chmod +x "$BUN"
fi
if [ "${SKIP_RUN:-0}" = "1" ]; then
  # x64 CI: cannot execute the aarch64 base, read the version string instead.
  BUN_VER="$(python3 - "$BUN" <<'PY'
import re, sys
d = open(sys.argv[1], 'rb').read()
pat = rb'(?:bun-v|Bun v)(\d+\.\d+\.\d+(?:-canary[^\s"\\]*)?)'
m = re.search(pat, d)
print(m.group(1).decode() if m else 'unknown')
PY
)"
else
  BUN_VER="$("$BUN" --revision 2>/dev/null || "$BUN" --version)"
fi

# 3. extract the standalone module graph ([u64 len][graph][Offsets32][trailer])
GRAPH="$WORK/claude-graph.bin"
python3 "$ROOT/tools/extract_graph.py" "$CLAUDE_BIN" "$GRAPH" > "$WORK/extract-report.json"
echo "build: graph extracted ($(stat -c%s "$GRAPH") bytes)" >&2

# 3b. Termux adaptations (disable native bfs/ugrep shell shadowing, etc.)
GRAPH_ADAPTED="$WORK/claude-graph-adapted.bin"
python3 "$ROOT/tools/adapt_graph.py" "$GRAPH" "$GRAPH_ADAPTED" > "$WORK/adapt-report.log"
echo "build: adaptations applied ($(cat "$WORK/adapt-report.log" | head -1))" >&2

# 4. graft onto the Android Bun ELF (BUN_COMPILED.size + PT_LOAD surgery).
#    Build to a temp path and rename: safe even while dist/claude is running.
python3 "$ROOT/tools/revive_patch.py" --bun "$BUN" --graph "$GRAPH_ADAPTED" --out "$DIST/.claude.new" > "$WORK/revive-report.log" 2>&1
mv -f "$DIST/.claude.new" "$DIST/claude"
chmod +x "$DIST/claude"
echo "build: grafted ($(stat -c%s "$DIST/claude") bytes)" >&2

# 5. verify (SKIP_RUN=1 for x64 CI runners: structure check only, no execution)
if [ "${SKIP_RUN:-0}" = "1" ]; then
  python3 - "$DIST/claude" <<'PY'
import struct, sys
with open(sys.argv[1], 'rb') as f:
    h = f.read(20)
assert h[:4] == b'\x7fELF', 'not an ELF'
assert struct.unpack_from('<H', h, 18)[0] == 0xB7, 'not aarch64'
print(f'build: ELF aarch64 OK ({sys.argv[1]})')
PY
  OUT_VER="$VER (not executed; SKIP_RUN=1)"
else
  OUT_VER="$("$DIST/claude" --version)"
  case "$OUT_VER" in
    "$VER"*) ;;
    *) echo "build: version mismatch: expected $VER, got $OUT_VER" >&2; exit 1 ;;
  esac
fi

# 6. build manifest + pinned-base drift check
OUT_SHA="$(sha256sum "$DIST/claude" | cut -d' ' -f1)"
OUT_SIZE="$(stat -c%s "$DIST/claude")"
BUN_SHA="$(sha256sum "$BUN" | cut -d' ' -f1)"
GRAPH_SHA="$(sha256sum "$GRAPH_ADAPTED" | cut -d' ' -f1)"
PINNED_BUN_SHA="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["base_bun"]["binary_sha256"])' "$ROOT/versions.json" 2>/dev/null || true)"
if [ -n "$PINNED_BUN_SHA" ] && [ "$BUN_SHA" != "$PINNED_BUN_SHA" ]; then
  echo "build: WARNING base Bun hash drifted from versions.json" >&2
  echo "       pinned=$PINNED_BUN_SHA" >&2
  echo "       actual=$BUN_SHA" >&2
fi
python3 - "$DIST/build-manifest.json" "$VER" "$OUT_VER" "$OUT_SHA" "$OUT_SIZE" \
        "$BUN_VER" "$BUN_SHA" "$GRAPH_SHA" "$(cat "$WORK/.claude-version" 2>/dev/null || echo "$VER")" <<'PY'
import json, sys, datetime
(path, ver, out_ver, out_sha, out_size, bun_ver, bun_sha, graph_sha, _v) = sys.argv[1:10]
json.dump({
    "claude": ver,
    "output_version": out_ver,
    "output_sha256": out_sha,
    "output_size": int(out_size),
    "graph_sha256": graph_sha,
    "adaptations": ["search_shadow"],
    "base_bun": {"version": bun_ver, "binary_sha256": bun_sha},
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}, open(path, "w"), indent=2)
print(path)
PY
# 7. refresh versions.json (the checked-in record) so it cannot drift behind
#    dist/build-manifest.json. Build facts are always written; the device
#    fields are written only when this build was actually executed here
#    (SKIP_RUN=1 builds are never run, so they record null instead of
#    leaving a stale device/date behind).
CLAUDE_SHA="$(sha256sum "$CLAUDE_BIN" | cut -d' ' -f1)"
DEVICE=""
VERIFIED_ON=""
if [ "${SKIP_RUN:-0}" != "1" ]; then
  REL="$(getprop ro.build.version.release 2>/dev/null || true)"
  ARCH="$(uname -m)"
  if [ -n "$REL" ]; then DEVICE="Android $REL / $ARCH"; else DEVICE="$(uname -s) / $ARCH"; fi
  VERIFIED_ON="$(date -u +%Y-%m-%d)"
fi
python3 - "$ROOT/versions.json" "$VER" "$CLAUDE_SHA" "$BUN_VER" "$BUN_SHA" \
        "$OUT_SHA" "$OUT_SIZE" "$GRAPH_SHA" "$DEVICE" "$VERIFIED_ON" "$BUN_URL" <<'PY'
import json, sys
(path, ver, claude_sha, bun_ver, bun_sha,
 out_sha, out_size, graph_sha, device, verified_on, bun_url) = sys.argv[1:12]
try:
    doc = json.load(open(path))
except (OSError, ValueError):
    doc = {}
doc["claude"] = ver
doc["claude_linux_arm64_sha256"] = claude_sha
bun = doc.setdefault("base_bun", {})
bun["version"] = bun_ver
bun["binary_sha256"] = bun_sha
bun.setdefault("url", bun_url)
bun.setdefault("note", "rolling canary tag; if the extracted binary hash drifts, the graph format may have changed")
doc["verified_output"] = {
    "file": "dist/claude",
    "sha256": out_sha,
    "size": int(out_size),
    "graph_sha256": graph_sha,
    "adaptations": ["search_shadow"],
    # verified_on = the built binary was executed here and reported the
    # expected version (build.sh step 5). Deeper checks (TUI, tools) stay manual.
    "device": device or None,
    "verified_on": verified_on or None,
}
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
print("build: versions.json refreshed" + (
    f" (executed on {device}, {verified_on})" if device else " (device fields null: SKIP_RUN)"), file=sys.stderr)
PY
# keep the per-build fingerprint in evidence/ current (docs/format.md §6)
cp -f "$DIST/build-manifest.json" "$ROOT/evidence/build-manifest.json"
echo "build: OK claude=$OUT_VER base-bun=$BUN_VER -> $DIST/claude"
