#!/data/data/com.termux/files/usr/bin/bash
# Build claude: graft Claude Code's standalone module graph onto an
# Android (bionic) Bun ELF. Zero glibc, no ptrace, no proot.
#
# Usage: build.sh [VERSION|latest]
# Env:   BUN_URL  override the pinned Android Bun base
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-latest}"
BUN_URL="${BUN_URL:-https://github.com/wmdhs12138/claude-code-termux/releases/download/bun-base-1.4.3-canary.1-5fce36ebb/bun-linux-aarch64-android.zip}"
REFRESH_BASE="${REFRESH_BASE:-0}"
WORK="$ROOT/work"
DIST="$ROOT/dist"
mkdir -p "$WORK" "$DIST" "$ROOT/evidence"

# Fixed staging paths are safe only when one build owns them. This also keeps
# two simultaneous `claude update` processes from corrupting shared work files.
exec 9>"$ROOT/.build.lock"
if ! flock -n 9; then
  echo "build: another build is already running ($ROOT/.build.lock)" >&2
  exit 1
fi
if [ "$REFRESH_BASE" != "0" ] && [ "$REFRESH_BASE" != "1" ]; then
  echo "build: REFRESH_BASE must be 0 or 1" >&2
  exit 2
fi

BINARY_BACKUP="$DIST/.claude.before-promote"
MANIFEST_BACKUP="$DIST/.build-manifest.before-promote"
VERSIONS_BACKUP="$ROOT/.versions.json.before-promote"
EVIDENCE_BACKUP="$ROOT/evidence/.build-manifest.before-promote"
PROMOTION_MARKER="$ROOT/.promotion-in-progress"

# Recover the old complete generation before reading its lock file. A SIGKILL
# cannot run traps, so the marker is the durable indication of an interrupted
# multi-file promotion.
if [ -e "$PROMOTION_MARKER" ]; then
  read -r REC_BINARY REC_MANIFEST REC_VERSIONS REC_EVIDENCE < "$PROMOTION_MARKER" || {
    echo "build: corrupt promotion marker; refusing unsafe recovery" >&2
    exit 1
  }
  case "$REC_BINARY$REC_MANIFEST$REC_VERSIONS$REC_EVIDENCE" in
    *[!01]*|?????*|???|??|?|"") echo "build: corrupt promotion marker; refusing unsafe recovery" >&2; exit 1 ;;
  esac
  if [ -e "$BINARY_BACKUP" ]; then cp -pf "$BINARY_BACKUP" "$DIST/claude"; elif [ "$REC_BINARY" = "0" ]; then rm -f "$DIST/claude"; fi
  if [ -e "$MANIFEST_BACKUP" ]; then cp -pf "$MANIFEST_BACKUP" "$DIST/build-manifest.json"; elif [ "$REC_MANIFEST" = "0" ]; then rm -f "$DIST/build-manifest.json"; fi
  if [ -e "$VERSIONS_BACKUP" ]; then cp -pf "$VERSIONS_BACKUP" "$ROOT/versions.json"; elif [ "$REC_VERSIONS" = "0" ]; then rm -f "$ROOT/versions.json"; fi
  if [ -e "$EVIDENCE_BACKUP" ]; then cp -pf "$EVIDENCE_BACKUP" "$ROOT/evidence/build-manifest.json"; elif [ "$REC_EVIDENCE" = "0" ]; then rm -f "$ROOT/evidence/build-manifest.json"; fi
  rm -f "$PROMOTION_MARKER"
fi

# The checked-in lock is a security boundary, not optional build metadata.
# Refuse malformed/missing values instead of silently replacing them later.
read -r PINNED_CLAUDE_VER PINNED_CLAUDE_SHA PINNED_BUN_ARCHIVE_SHA PINNED_BUN_SHA < <(
  python3 - "$ROOT/versions.json" <<'PY'
import json, re, sys
try:
    with open(sys.argv[1]) as f:
        doc = json.load(f)
    claude = doc["claude"]
    claude_sha = doc["claude_linux_arm64_sha256"]
    bun_archive_sha = doc["base_bun"]["archive_sha256"]
    bun_sha = doc["base_bun"]["binary_sha256"]
except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
    raise SystemExit(f"invalid versions.json: {exc}") from None
if not isinstance(claude, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", claude):
    raise SystemExit("versions.json has an invalid Claude version")
for name, value in (("Claude", claude_sha), ("Bun archive", bun_archive_sha),
                    ("Bun binary", bun_sha)):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise SystemExit(f"versions.json has an invalid {name} sha256")
print(claude, claude_sha.lower(), bun_archive_sha.lower(), bun_sha.lower())
PY
)

CANONICAL_BUN_DIR="$WORK/bun-android"
CANONICAL_BUN_ZIP="$WORK/bun-android.zip"
BASE_BACKUP_DIR="$WORK/.bun-android.before-refresh"

# Recover deterministically if a previous process was killed during base-cache
# promotion. Whichever directory matches the current lock is the known-good one.
if [ -d "$BASE_BACKUP_DIR" ]; then
  current_sha=""
  backup_sha="$(sha256sum "$BASE_BACKUP_DIR/bun" 2>/dev/null | cut -d' ' -f1 || true)"
  [ ! -x "$CANONICAL_BUN_DIR/bun" ] || current_sha="$(sha256sum "$CANONICAL_BUN_DIR/bun" | cut -d' ' -f1)"
  if [ "$current_sha" = "$PINNED_BUN_SHA" ]; then
    rm -rf "$BASE_BACKUP_DIR"
  elif [ "$backup_sha" = "$PINNED_BUN_SHA" ]; then
    rm -rf "$CANONICAL_BUN_DIR"
    mv "$BASE_BACKUP_DIR" "$CANONICAL_BUN_DIR"
  else
    echo "build: cannot recover interrupted Bun cache promotion" >&2
    exit 1
  fi
fi

# A refresh downloads into a staging cache and leaves the known-good cache in
# place until every build verification succeeds.
if [ "$REFRESH_BASE" = "1" ]; then
  BUN_DIR="$WORK/.bun-android.refresh"
  BUN_ZIP="$WORK/.bun-android.refresh.zip"
  rm -rf "$BUN_DIR" "$BUN_ZIP"
else
  BUN_DIR="$CANONICAL_BUN_DIR"
  BUN_ZIP="$CANONICAL_BUN_ZIP"
fi
BASE_DOWNLOADED_THIS_RUN=0
BUN_ARCHIVE_SHA="$PINNED_BUN_ARCHIVE_SHA"
BASE_SWAPPED=0
BASE_HAD_OLD=0
PROMOTION_STARTED=0
PROMOTION_COMMITTED=0
HAD_OLD_BINARY=0
HAD_OLD_MANIFEST=0
HAD_OLD_VERSIONS=0
HAD_OLD_EVIDENCE=0

CANDIDATE="$DIST/.claude.new"
MANIFEST_NEW="$DIST/.build-manifest.new"
VERSIONS_NEW="$ROOT/.versions.json.new"
EVIDENCE_NEW="$ROOT/evidence/.build-manifest.new"
BINARY_BACKUP="$DIST/.claude.before-promote"
MANIFEST_BACKUP="$DIST/.build-manifest.before-promote"
VERSIONS_BACKUP="$ROOT/.versions.json.before-promote"
EVIDENCE_BACKUP="$ROOT/evidence/.build-manifest.before-promote"
PROMOTION_MARKER="$ROOT/.promotion-in-progress"
rm -f "$MANIFEST_NEW" "$VERSIONS_NEW" "$EVIDENCE_NEW" "$PROMOTION_MARKER.new" \
      "$BINARY_BACKUP" "$MANIFEST_BACKUP" "$VERSIONS_BACKUP" "$EVIDENCE_BACKUP"

cleanup() {
  if [ "$PROMOTION_STARTED" = "1" ] && [ "$PROMOTION_COMMITTED" != "1" ]; then
    if [ -e "$BINARY_BACKUP" ]; then cp -pf "$BINARY_BACKUP" "$DIST/claude"; elif [ "$HAD_OLD_BINARY" = "0" ]; then rm -f "$DIST/claude"; fi
    if [ -e "$MANIFEST_BACKUP" ]; then cp -pf "$MANIFEST_BACKUP" "$DIST/build-manifest.json"; elif [ "$HAD_OLD_MANIFEST" = "0" ]; then rm -f "$DIST/build-manifest.json"; fi
    if [ -e "$VERSIONS_BACKUP" ]; then cp -pf "$VERSIONS_BACKUP" "$ROOT/versions.json"; elif [ "$HAD_OLD_VERSIONS" = "0" ]; then rm -f "$ROOT/versions.json"; fi
    if [ -e "$EVIDENCE_BACKUP" ]; then cp -pf "$EVIDENCE_BACKUP" "$ROOT/evidence/build-manifest.json"; elif [ "$HAD_OLD_EVIDENCE" = "0" ]; then rm -f "$ROOT/evidence/build-manifest.json"; fi
    rm -f "$PROMOTION_MARKER"
    rm -f "$BINARY_BACKUP" "$MANIFEST_BACKUP" "$VERSIONS_BACKUP" "$EVIDENCE_BACKUP"
  fi
  if [ "$BASE_SWAPPED" = "1" ] && [ "$PROMOTION_COMMITTED" != "1" ]; then
    rm -rf "$CANONICAL_BUN_DIR"
    if [ "$BASE_HAD_OLD" = "1" ]; then mv "$BASE_BACKUP_DIR" "$CANONICAL_BUN_DIR"; fi
  fi
  if [ "$REFRESH_BASE" = "1" ]; then rm -rf "$BUN_DIR" "$BUN_ZIP"; fi
}
trap cleanup EXIT

# Child fetches share this lock rather than trying to acquire it again.
export CLAUDE_CODE_TERMUX_LOCK_HELD=1

fail_before_promote() {
  echo "build: $1" >&2
  echo "build: $DIST/claude and its metadata were left unchanged" >&2
  exit 1
}

# A build that fails verification must not take a working dist/claude with it,
# and the one message that matters then is "your previous build is untouched" --
# say it loudly instead of dying on a bare `set -e`.
die_kept() {
  echo "build: $1" >&2
  echo "build: $DIST/claude was left exactly as it was; a failed build never" >&2
  echo "build: replaces it. The candidate is at $CANDIDATE" >&2
  echo "build: To fall back to a known-good version: make build VERSION=<version>" >&2
  exit 1
}

# 1. official Claude Code linux-arm64 binary (checksum-verified)
CLAUDE_BIN="$(bash "$ROOT/scripts/fetch-claude.sh" "$VERSION" "$WORK")"
VER="$(cat "$WORK/.claude-version")"
CLAUDE_SHA="$(sha256sum "$CLAUDE_BIN" | cut -d' ' -f1)"
if [ "$VER" = "$PINNED_CLAUDE_VER" ] && [ -n "$PINNED_CLAUDE_SHA" ] \
   && [ "$CLAUDE_SHA" != "$PINNED_CLAUDE_SHA" ]; then
  fail_before_promote "Claude $VER hash differs from versions.json (expected $PINNED_CLAUDE_SHA, got $CLAUDE_SHA)"
fi

# 2. Android Bun base (bionic ELF)
BUN="$BUN_DIR/bun"
if [ ! -x "$BUN" ]; then
  echo "build: fetching Android Bun base..." >&2
  curl -fL --retry 3 -o "$BUN_ZIP" "$BUN_URL"
  BUN_ARCHIVE_SHA="$(sha256sum "$BUN_ZIP" | cut -d' ' -f1)"
  if [ "$BUN_ARCHIVE_SHA" != "$PINNED_BUN_ARCHIVE_SHA" ] \
     && [ "$REFRESH_BASE" != "1" ]; then
    fail_before_promote "base Bun archive hash differs from versions.json (expected $PINNED_BUN_ARCHIVE_SHA, got $BUN_ARCHIVE_SHA)"
  fi
  rm -rf "$BUN_DIR" "$BUN_ZIP.d"
  mkdir -p "$BUN_DIR"
  unzip -o -j "$BUN_ZIP" "*/bun" -d "$BUN_DIR" >/dev/null
  chmod +x "$BUN"
  BASE_DOWNLOADED_THIS_RUN=1
fi
BUN_SHA="$(sha256sum "$BUN" | cut -d' ' -f1)"
if [ "$BUN_SHA" != "$PINNED_BUN_SHA" ]; then
  if [ "$REFRESH_BASE" != "1" ] || [ "$BASE_DOWNLOADED_THIS_RUN" != "1" ]; then
    fail_before_promote "base Bun hash differs from versions.json (expected $PINNED_BUN_SHA, got $BUN_SHA); run 'make refresh-base' to download, validate and accept it explicitly"
  fi
  echo "build: base Bun refresh explicitly allowed" >&2
  echo "       pinned=$PINNED_BUN_SHA" >&2
  echo "       actual=$BUN_SHA" >&2
fi
if [ "${SKIP_RUN:-0}" = "1" ]; then
  # x64 CI: cannot execute the aarch64 base, read the version string instead.
  # Prefer the full revision form ("Bun v1.4.3-canary.1+86771d09f"): a bare
  # "Bun v1.4.3" also occurs earlier in the binary, and taking the leftmost
  # match would drop the canary identity that --revision reports on a device.
  BUN_VER="$(python3 - "$BUN" <<'PY'
import re, sys
d = open(sys.argv[1], 'rb').read()
for pat in (rb'(?:bun-v|Bun v)(\d+\.\d+\.\d+[-+][0-9A-Za-z.+\-]{2,})',
            rb'(?:bun-v|Bun v)(\d+\.\d+\.\d+)'):
    m = re.search(pat, d)
    if m:
        print(m.group(1).decode())
        break
else:
    print('unknown')
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
python3 "$ROOT/tools/adapt_graph.py" "$GRAPH" "$GRAPH_ADAPTED" --report "$WORK/adapt-report.json" > "$WORK/adapt-report.log"
echo "build: adaptations applied ($(head -1 "$WORK/adapt-report.log"))" >&2

# 4. graft onto the Android Bun ELF (BUN_COMPILED.size + PT_LOAD surgery).
#    Staged to a temp path and left there: it does NOT replace dist/claude until
#    step 5 has passed. A broken graft is what a rolled base Bun produces, and
#    swapping a working binary out for it leaves nothing to fall back to.
#    (Writing the staging file is safe even while dist/claude is running.)
python3 "$ROOT/tools/revive_patch.py" --bun "$BUN" --graph "$GRAPH_ADAPTED" --out "$CANDIDATE" > "$WORK/revive-report.log" 2>&1
chmod +x "$CANDIDATE"
echo "build: grafted ($(stat -c%s "$CANDIDATE") bytes, staged)" >&2

# 5. verify the staged candidate.
#    The structural check walks the graft closure (size field -> payload length
#    -> trailer -> module table) and runs everywhere, including the x64 CI
#    runner, which cannot execute an aarch64 bionic binary at all. The exec
#    check runs only where the artifact can actually run.
python3 "$ROOT/tools/verify_graft.py" "$CANDIDATE" "$(stat -c%s "$GRAPH_ADAPTED")" | tee "$WORK/verify-graft.json"
if [ "${SKIP_RUN:-0}" = "1" ]; then
  OUT_VER="$VER (not executed; SKIP_RUN=1)"
else
  # A segfault here is the classic rolled-base failure, and it is exactly the
  # case where the user most needs to be told the old binary is still there --
  # so it gets the same treatment as a version mismatch, not a bare set -e exit.
  if ! OUT_VER="$("$CANDIDATE" --version)"; then
    die_kept "the staged candidate did not run to completion (see the shell's own message above)"
  fi
  case "$OUT_VER" in
    "$VER"*) ;;
    *) die_kept "version mismatch: expected $VER, got $OUT_VER" ;;
  esac
fi

# 6. Prepare every tracked credential before replacing the working binary.
# Cross-file atomicity is impossible, but this makes post-promotion failures a
# tiny sequence of local renames rather than JSON generation or disk writes.
OUT_SHA="$(sha256sum "$CANDIDATE" | cut -d' ' -f1)"
OUT_SIZE="$(stat -c%s "$CANDIDATE")"
GRAPH_SHA="$(sha256sum "$GRAPH_ADAPTED" | cut -d' ' -f1)"
python3 - "$MANIFEST_NEW" "$VER" "$CLAUDE_SHA" "$OUT_VER" "$OUT_SHA" "$OUT_SIZE" \
        "$BUN_VER" "$BUN_ARCHIVE_SHA" "$BUN_SHA" "$BUN_URL" "$GRAPH_SHA" \
        "$WORK/adapt-report.json" "$WORK/verify-graft.json" <<'PY'
import json, sys, datetime
(path, ver, claude_sha, out_ver, out_sha, out_size, bun_ver, bun_archive_sha,
 bun_sha, bun_url, graph_sha, adapt_path, graft_path) = sys.argv[1:14]
def load(p):
    with open(p) as f:
        return json.load(f)
doc = {
    "claude": ver,
    "claude_linux_arm64_sha256": claude_sha,
    "output_version": out_ver,
    "output_sha256": out_sha,
    "output_size": int(out_size),
    "graph_sha256": graph_sha,
    # Read from the adaptation run itself, never a hardcoded list: a credential
    # that understates what the build did is worse than no credential.
    "adaptations": load(adapt_path)["adaptations"],
    # What the structural check verified about this exact artifact (step 5).
    "graft": load(graft_path),
    "base_bun": {
        "version": bun_ver,
        "url": bun_url,
        "archive_sha256": bun_archive_sha,
        "binary_sha256": bun_sha,
    },
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
print(path)
PY
# 7. Prepare versions.json (the checked-in record) beside the old file. Build
#    facts are always written; device fields are written only after execution
#    on this device (SKIP_RUN builds record null instead of stale claims).
DEVICE=""
VERIFIED_ON=""
if [ "${SKIP_RUN:-0}" != "1" ]; then
  REL="$(getprop ro.build.version.release 2>/dev/null || true)"
  ARCH="$(uname -m)"
  if [ -n "$REL" ]; then DEVICE="Android $REL / $ARCH"; else DEVICE="$(uname -s) / $ARCH"; fi
  VERIFIED_ON="$(date -u +%Y-%m-%d)"
fi
python3 - "$ROOT/versions.json" "$VERSIONS_NEW" "$VER" "$CLAUDE_SHA" "$BUN_VER" \
        "$BUN_ARCHIVE_SHA" "$BUN_SHA" "$OUT_SHA" "$OUT_SIZE" "$GRAPH_SHA" "$DEVICE" \
        "$VERIFIED_ON" "$BUN_URL" "$WORK/adapt-report.json" <<'PY'
import json, sys
(source_path, path, ver, claude_sha, bun_ver, bun_archive_sha, bun_sha,
 out_sha, out_size, graph_sha, device, verified_on, bun_url,
 adapt_path) = sys.argv[1:15]
try:
    doc = json.load(open(source_path))
except (OSError, ValueError):
    doc = {}
doc["claude"] = ver
doc["claude_linux_arm64_sha256"] = claude_sha
bun = doc.setdefault("base_bun", {})
bun["version"] = bun_ver
bun["archive_sha256"] = bun_archive_sha
bun["binary_sha256"] = bun_sha
bun["url"] = bun_url
bun["note"] = "immutable mirrored release; update only after Android build and smoke verification"
doc["verified_output"] = {
    "file": "dist/claude",
    "sha256": out_sha,
    "size": int(out_size),
    "graph_sha256": graph_sha,
    "adaptations": json.load(open(adapt_path))["adaptations"],
    # verified_on = the built binary was executed here and reported the
    # expected version (build.sh step 5). Deeper checks (TUI, tools) stay manual.
    "device": device or None,
    "verified_on": verified_on or None,
}
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
print("build: versions.json prepared" + (
    f" (executed on {device}, {verified_on})" if device else " (device fields null: SKIP_RUN)"), file=sys.stderr)
PY
cp -f "$MANIFEST_NEW" "$EVIDENCE_NEW"

# 8. All expensive work and file generation succeeded. Record which old files
# exist, then rename them aside. The durable state makes both forward promotion
# and repeated crash recovery safe without duplicating the ~225 MB binary.
[ ! -e "$DIST/claude" ] || HAD_OLD_BINARY=1
[ ! -e "$DIST/build-manifest.json" ] || HAD_OLD_MANIFEST=1
[ ! -e "$ROOT/versions.json" ] || HAD_OLD_VERSIONS=1
[ ! -e "$ROOT/evidence/build-manifest.json" ] || HAD_OLD_EVIDENCE=1
printf '%s %s %s %s\n' "$HAD_OLD_BINARY" "$HAD_OLD_MANIFEST" \
  "$HAD_OLD_VERSIONS" "$HAD_OLD_EVIDENCE" > "$PROMOTION_MARKER.new"
mv -f "$PROMOTION_MARKER.new" "$PROMOTION_MARKER"
PROMOTION_STARTED=1
if [ "$HAD_OLD_BINARY" = "1" ]; then mv "$DIST/claude" "$BINARY_BACKUP"; fi
if [ "$HAD_OLD_MANIFEST" = "1" ]; then mv "$DIST/build-manifest.json" "$MANIFEST_BACKUP"; fi
if [ "$HAD_OLD_VERSIONS" = "1" ]; then mv "$ROOT/versions.json" "$VERSIONS_BACKUP"; fi
if [ "$HAD_OLD_EVIDENCE" = "1" ]; then mv "$ROOT/evidence/build-manifest.json" "$EVIDENCE_BACKUP"; fi

if [ "$REFRESH_BASE" = "1" ]; then
  if [ -d "$CANONICAL_BUN_DIR" ]; then
    mv "$CANONICAL_BUN_DIR" "$BASE_BACKUP_DIR"
    BASE_HAD_OLD=1
  fi
  BASE_SWAPPED=1
  mv "$BUN_DIR" "$CANONICAL_BUN_DIR"
fi

mv -f "$CANDIDATE" "$DIST/claude"
mv -f "$MANIFEST_NEW" "$DIST/build-manifest.json"
mv -f "$VERSIONS_NEW" "$ROOT/versions.json"
mv -f "$EVIDENCE_NEW" "$ROOT/evidence/build-manifest.json"
rm -f "$PROMOTION_MARKER"
PROMOTION_COMMITTED=1
rm -f "$BINARY_BACKUP" "$MANIFEST_BACKUP" "$VERSIONS_BACKUP" "$EVIDENCE_BACKUP"
rm -rf "$BASE_BACKUP_DIR"
if [ "$REFRESH_BASE" = "1" ]; then
  rm -f "$CANONICAL_BUN_ZIP"
  mv "$BUN_ZIP" "$CANONICAL_BUN_ZIP"
fi
echo "build: OK claude=$OUT_VER base-bun=$BUN_VER -> $DIST/claude"
