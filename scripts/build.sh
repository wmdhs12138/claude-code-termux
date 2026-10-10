#!/data/data/com.termux/files/usr/bin/bash
# Build dist/claude: graft Claude Code's standalone module graph onto a Bionic
# (Android) Bun ELF. Zero glibc, no ptrace, no proot.
#
#   scripts/build.sh [VERSION|latest]
#
# Output: dist/claude and dist/build-manifest.json, replaced only after every
# check below has passed. Downloads, intermediate graphs and reports stay in
# work/. Nothing tracked by git is written, except versions.json on an explicit
# REFRESH_BASE=1.
#
# Env:
#   REFRESH_BASE=1 [BUN_URL=<zip>]  build with a new Bun base and, once every
#                        check has passed, pin its hashes in versions.json
#   CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE
#                        content-addressed Bun cache (default: work/bun-bases);
#                        `claude update` shares one across toolchains
#   SKIP_RUN=1           structural build only, where an aarch64 Bionic binary
#                        cannot execute (no version probe, no TUI smoke)
#   SMOKE_SECONDS=N      TUI smoke observation window (default 7)
#   CLAUDE_CODE_TERMUX_PROGRESS_FD=N
#                        the caller logs this script's output and wants only
#                        progress on fd N: download bars and one status line
#
# Contract: `claude update` (runtime/self-update.sh) and
# scripts/install-approved.sh run this script from a downloaded toolchain tag,
# then read dist/claude and the manifest fields claude,
# claude_linux_arm64_sha256, base_bun.archive_sha256/binary_sha256 and
# tui_smoke. Installed binaries depend on that: keep the entry point, the output
# paths and those fields stable.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-latest}"
REFRESH_BASE="${REFRESH_BASE:-0}"
WORK="$ROOT/work"
DIST="$ROOT/dist"
mkdir -p "$WORK" "$DIST"

# Fixed staging paths are safe only when one build owns them. This also keeps
# two simultaneous `claude update` processes from corrupting shared work files.
exec 9>"$WORK/.build.lock"
if ! flock -n 9; then
  echo "build: another build is already running ($WORK/.build.lock)" >&2
  exit 1
fi
if [ "$REFRESH_BASE" != "0" ] && [ "$REFRESH_BASE" != "1" ]; then
  echo "build: REFRESH_BASE must be 0 or 1" >&2
  exit 2
fi
if [ "$REFRESH_BASE" = "1" ] && [ "${SKIP_RUN:-0}" = "1" ]; then
  # A base is accepted only after the grafted candidate ran and rendered.
  echo "build: REFRESH_BASE=1 needs a device that can execute the candidate; drop SKIP_RUN" >&2
  exit 2
fi

# versions.json pins the Bun base. It is a security boundary, not optional
# build metadata: refuse malformed or missing values instead of guessing.
read -r PIN_BUN_URL PIN_BUN_ARCHIVE_SHA PIN_BUN_SHA < <(
  python3 - "$ROOT/versions.json" <<'PY'
import json, re, sys
try:
    with open(sys.argv[1]) as f:
        base = json.load(f)["base_bun"]
    url, archive_sha, binary_sha = base["url"], base["archive_sha256"], base["binary_sha256"]
except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
    raise SystemExit(f"invalid versions.json: {exc}") from None
if not isinstance(url, str) or not re.fullmatch(r"https://\S+", url):
    raise SystemExit("versions.json has an invalid Bun base URL")
for name, value in (("Bun archive", archive_sha), ("Bun binary", binary_sha)):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise SystemExit(f"versions.json has an invalid {name} sha256")
print(url, archive_sha.lower(), binary_sha.lower())
PY
)
BUN_URL="${BUN_URL:-$PIN_BUN_URL}"
BUN_CACHE="${CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE:-$WORK/bun-bases}"

CANDIDATE="$DIST/.claude.new"
MANIFEST_NEW="$DIST/.build-manifest.new"
REFRESH_STAGE=""
cleanup() {
  rm -f "$MANIFEST_NEW"
  if [ -n "$REFRESH_STAGE" ]; then rm -rf "$REFRESH_STAGE"; fi
}
trap cleanup EXIT

# Child fetches share this lock rather than trying to acquire it again.
export CLAUDE_CODE_TERMUX_LOCK_HELD=1

# One line for whoever watches the terminal while the details go to a log.
progress() {
  if [ -n "${CLAUDE_CODE_TERMUX_PROGRESS_FD:-}" ]; then
    echo "$1" >&"$CLAUDE_CODE_TERMUX_PROGRESS_FD"
  fi
}

fail_before_promote() {
  echo "build: $1" >&2
  echo "build: $DIST/claude and its manifest were left unchanged" >&2
  exit 1
}

# A build that fails verification must not take a working dist/claude with it,
# and the one message that matters then is "your previous build is untouched" --
# say it loudly instead of dying on a bare `set -e`.
die_kept() {
  echo "build: $1" >&2
  echo "build: $DIST/claude was left exactly as it was; a failed build never" >&2
  echo "build: replaces it. The candidate is at $CANDIDATE" >&2
  echo "build: To fall back to a known-good version: scripts/build.sh <version>" >&2
  exit 1
}

# 1. official Claude Code linux-arm64 binary (checksum-verified against
#    Anthropic's release manifest by fetch-claude.sh)
CLAUDE_BIN="$(bash "$ROOT/scripts/fetch-claude.sh" "$VERSION" "$WORK")"
VER="$(cat "$WORK/.claude-version")"
CLAUDE_SHA="$(sha256sum "$CLAUDE_BIN" | cut -d' ' -f1)"

# 2. Android Bun base (Bionic ELF), content-addressed by its SHA-256 in
#    $BUN_CACHE. A refresh hashes the candidate first and feeds the same cache,
#    so the pinned base is never touched and a failed refresh changes nothing.
if [ "$REFRESH_BASE" = "1" ]; then
  REFRESH_STAGE="$(mktemp -d "$WORK/.bun-refresh.XXXXXX")"
  echo "build: downloading candidate Bun base $BUN_URL" >&2
  bash "$ROOT/scripts/download.sh" "$BUN_URL" "$REFRESH_STAGE/bun.zip" "  Downloading the candidate Bun base"
  unzip -o -j "$REFRESH_STAGE/bun.zip" "*/bun" -d "$REFRESH_STAGE" >/dev/null
  BUN_ARCHIVE_SHA="$(sha256sum "$REFRESH_STAGE/bun.zip" | cut -d' ' -f1)"
  BUN_SHA="$(sha256sum "$REFRESH_STAGE/bun" | cut -d' ' -f1)"
  echo "build: base Bun refresh explicitly allowed" >&2
  echo "       pinned=$PIN_BUN_SHA" >&2
  echo "       actual=$BUN_SHA" >&2
  BUN_SOURCE="file://$REFRESH_STAGE/bun.zip"
else
  BUN_ARCHIVE_SHA="$PIN_BUN_ARCHIVE_SHA"
  BUN_SHA="$PIN_BUN_SHA"
  BUN_SOURCE="$BUN_URL"
fi
if ! BUN="$(bash "$ROOT/scripts/ensure-bun-base.sh" "$BUN_CACHE" "$BUN_SOURCE" \
            "$BUN_ARCHIVE_SHA" "$BUN_SHA")"; then
  fail_before_promote "could not provide the Bun base pinned in versions.json; to accept a different base run REFRESH_BASE=1 BUN_URL=<zip> scripts/build.sh"
fi
if [ "${SKIP_RUN:-0}" = "1" ]; then
  # Cannot execute the aarch64 base here, so read the version string instead.
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

progress "  Building and verifying..."

# 3. extract the standalone module graph ([u64 len][graph][Offsets32][trailer])
GRAPH="$WORK/claude-graph.bin"
python3 "$ROOT/tools/extract_graph.py" "$CLAUDE_BIN" "$GRAPH" > "$WORK/extract-report.json"
echo "build: graph extracted ($(stat -c%s "$GRAPH") bytes)" >&2

# 3a. Native Ink ABI guard: runtime/40-cell-segmenter.js is written against
#     the Bun.ant.CellSegmenter member surface this graph uses. A drift turns
#     into a blank terminal at the user's first launch, so fail here instead;
#     the candidate is not grafted and dist/claude stays untouched.
if ! python3 "$ROOT/tools/check_native_abi.py" "$GRAPH" --report "$WORK/native-abi.json" > "$WORK/native-abi.log" 2>&1; then
  fail_before_promote "$(tail -1 "$WORK/native-abi.log")"
fi
echo "build: $(head -1 "$WORK/native-abi.log")" >&2

# 3b. Termux adaptations (disable native bfs/ugrep shell shadowing, etc.)
GRAPH_SEARCH_ADAPTED="$WORK/claude-graph-search-adapted.bin"
python3 "$ROOT/tools/adapt_graph.py" "$GRAPH" "$GRAPH_SEARCH_ADAPTED" --report "$WORK/adapt-report.json" > "$WORK/adapt-report.log"
echo "build: adaptations applied ($(head -1 "$WORK/adapt-report.log"))" >&2

# 3c. Embed runtime/ (Android defaults, self-updater, Bun.ant shims) into the
#     entry module. The resulting ELF starts directly: no launcher or
#     BUN_OPTIONS preload path is required at runtime.
RUNTIME_JS="$WORK/runtime.js"
python3 "$ROOT/tools/assemble_runtime.py" "$ROOT/runtime" "$RUNTIME_JS" \
  --report "$WORK/runtime.json" > "$WORK/runtime.log"
GRAPH_ADAPTED="$WORK/claude-graph-adapted.bin"
python3 "$ROOT/tools/embed_preload.py" "$GRAPH_SEARCH_ADAPTED" "$RUNTIME_JS" \
  "$GRAPH_ADAPTED" --report "$WORK/embed-preload.json" > "$WORK/embed-preload.log"
echo "build: runtime embedded into the entry module ($(python3 -c 'import json,sys; print(", ".join(json.load(open(sys.argv[1]))["modules"]))' "$WORK/runtime.json"))" >&2

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
python3 "$ROOT/tools/verify_graft.py" "$CANDIDATE" "$(stat -c%s "$GRAPH_ADAPTED")" > "$WORK/verify-graft.json"
GRAFT_SUMMARY="$(python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["modules"], "modules, entry", d["entry_point"])' "$WORK/verify-graft.json")"
echo "build: graft verified ($GRAFT_SUMMARY)" >&2
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

# 5b. Render check. The checks above prove the artifact is structurally intact
#     and can print a version string; neither touches src/ink, so a graft whose
#     Bun.ant.CellSegmenter is broken -- the blank-terminal failure 2.1.271
#     introduced -- passes both and is only discovered at the user's first
#     launch. Render a real frame here, on the candidate rather than on
#     dist/claude, without a preload environment. This runs only
#     where the artifact can execute, which is the condition SKIP_RUN already
#     encodes (the x64 CI runner cannot run an aarch64 bionic binary at all).
SMOKE_RAN=0
SMOKE_REASON=""
SMOKE_SECONDS="${SMOKE_SECONDS:-7}"
if [ "${SKIP_RUN:-0}" = "1" ]; then
  SMOKE_REASON="SKIP_RUN=1"
  echo "build: TUI smoke skipped ($SMOKE_REASON)" >&2
else
  SMOKE_LOG="$WORK/tui-smoke.log"
  # USE_BUILTIN_RIPGREP matters because the embedded rg is a
  # linux binary, and DISABLE_AUTOUPDATER keeps Claude's auto-updater, which
  # now builds and swaps in the latest release (native_updater), from replacing
  # the candidate under test.
  if ! USE_BUILTIN_RIPGREP=0 DISABLE_AUTOUPDATER=1 \
       python3 "$ROOT/tools/tui_smoke.py" "$CANDIDATE" "$SMOKE_SECONDS" > "$SMOKE_LOG" 2>&1; then
    die_kept "the staged candidate did not render a TUI: $(tail -1 "$SMOKE_LOG")"
  fi
  SMOKE_RAN=1
  echo "build: TUI smoke passed ($(tail -1 "$SMOKE_LOG"))" >&2
fi
# Record it as a credential rather than leaving it in a log: the manifest is
# what a release is audited from, and "smoke: pass" living only in a build log
# cannot be told apart from a build that never ran the check.
python3 - "$WORK/tui-smoke.json" "$SMOKE_RAN" "$SMOKE_SECONDS" "$SMOKE_REASON" <<'PY'
import json, sys
path, ran, seconds, reason = sys.argv[1:5]
doc = {"ran": ran == "1", "seconds": float(seconds)}
if doc["ran"]:
    doc["result"] = "pass"
else:
    doc["skipped"] = reason or "skipped"
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PY

# 6. Write the manifest beside the candidate before anything is replaced, so
#    promotion is two local renames.
OUT_SHA="$(sha256sum "$CANDIDATE" | cut -d' ' -f1)"
OUT_SIZE="$(stat -c%s "$CANDIDATE")"
GRAPH_SHA="$(sha256sum "$GRAPH_ADAPTED" | cut -d' ' -f1)"
python3 - "$MANIFEST_NEW" "$VER" "$CLAUDE_SHA" "$OUT_VER" "$OUT_SHA" "$OUT_SIZE" \
        "$BUN_VER" "$BUN_ARCHIVE_SHA" "$BUN_SHA" "$BUN_URL" "$GRAPH_SHA" \
        "$WORK/adapt-report.json" "$WORK/embed-preload.json" "$WORK/verify-graft.json" \
        "$WORK/native-abi.json" "$WORK/tui-smoke.json" "$WORK/runtime.json" <<'PY'
import json, sys, datetime
(path, ver, claude_sha, out_ver, out_sha, out_size, bun_ver, bun_archive_sha,
 bun_sha, bun_url, graph_sha, adapt_path, embed_path, graft_path, abi_path,
 smoke_path, runtime_path) = sys.argv[1:18]
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
    "adaptations": load(adapt_path)["adaptations"] + load(runtime_path)["adaptations"],
    "embedded_preload": load(embed_path),
    # What the structural check verified about this exact artifact (step 5).
    "graft": load(graft_path),
    # The native Ink surface this graph used and runtime/40-cell-segmenter.js
    # was checked against (step 3a), so an ABI change is visible in the
    # release credential.
    "native_abi": load(abi_path),
    # Whether this exact artifact actually rendered a frame (step 5b). False on
    # a SKIP_RUN build, which never executed the binary -- never read it as
    # "the TUI was verified" without checking "ran".
    "tui_smoke": load(smoke_path),
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
PY

# 7. Promote. The old manifest goes first: an interruption between the renames
#    then leaves a binary without a manifest, never one paired with the
#    manifest of a different build.
rm -f "$DIST/build-manifest.json"
mv -f "$CANDIDATE" "$DIST/claude"
mv -f "$MANIFEST_NEW" "$DIST/build-manifest.json"

# 8. An accepted base refresh is the one write to a tracked file.
if [ "$REFRESH_BASE" = "1" ]; then
  python3 - "$ROOT/versions.json" "$BUN_VER" "$BUN_URL" "$BUN_ARCHIVE_SHA" "$BUN_SHA" <<'PY'
import json, os, sys
path, version, url, archive_sha, binary_sha = sys.argv[1:6]
with open(path) as f:
    doc = json.load(f)
doc["base_bun"] = {"version": version, "url": url,
                   "archive_sha256": archive_sha, "binary_sha256": binary_sha}
with open(path + ".new", "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
os.replace(path + ".new", path)
PY
  echo "build: versions.json now pins Bun base ${BUN_SHA:0:12} ($BUN_VER)" >&2
fi
echo "build: OK claude=$OUT_VER base-bun=$BUN_VER -> $DIST/claude"
