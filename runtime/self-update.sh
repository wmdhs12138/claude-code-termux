#!/usr/bin/env bash
# `claude update` for the Bionic graft. tools/assemble_runtime.py inlines this
# file into runtime/20-self-update.js, which runs it as
#
#   bash -c <script> claude-self-update TARGET FORCE CHECK CACHE_BASE
#
# so it can also be run directly: bash runtime/self-update.sh TARGET 0 1 ~/.cache
# It builds the latest CI-approved Release from its tagged toolchain and
# atomically replaces TARGET; on any failure TARGET is left untouched.
set -euo pipefail
target="$1"
force="$2"
check="$3"
cache_base="$4"

cleanup_claude_workdirs() {
  local root="$1"
  [ -d "$root" ] || return 0
  python3 - "$root" <<'PY'
import os
from pathlib import Path
import re
import shutil
import sys

root = Path(sys.argv[1])
patterns = (
    re.compile(r"toolchain-[0-9a-f]{40}"),
    re.compile(r"claude-\d+\.\d+\.\d+-toolchain-[0-9a-f]{40}"),
    re.compile(r"(?:download|update)\..+"),
)

def path_size(path):
    size = 0
    for parent, dirs, files in os.walk(path, topdown=True, followlinks=False):
        for name in files:
            try:
                size += os.lstat(os.path.join(parent, name)).st_size
            except OSError:
                pass
    return size

victims = []
for path in root.iterdir():
    if path.is_symlink() or not path.is_dir():
        continue
    if any(pattern.fullmatch(path.name) for pattern in patterns):
        victims.append(path)
freed = 0
for path in victims:
    freed += path_size(path)
    shutil.rmtree(path)
if victims:
    print(
        f"claude update: temporary cache cleanup: "
        f"{len(victims)} removed ({freed / (1024 * 1024):.1f} MiB freed)"
    )
PY
}

prune_bun_cache() {
  local root="$1"
  local protected_sha=""
  if [ "$#" -ge 2 ]; then protected_sha="$2"; fi
  local bun_keep
  bun_keep="$(printenv CLAUDE_CODE_TERMUX_BUN_CACHE_KEEP 2>/dev/null || printf 1)"
  if ! [[ "$bun_keep" =~ ^[0-9]+$ ]] || [ "$bun_keep" -lt 1 ]; then
    echo "claude update: invalid Bun cache retention '$bun_keep'; keeping 1" >&2
    bun_keep=1
  fi
  [ -d "$root/bun-bases" ] || return 0
  python3 - "$root/bun-bases" "$protected_sha" "$bun_keep" <<'PY'
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys

root = Path(sys.argv[1])
protected_sha = sys.argv[2]
keep = int(sys.argv[3])

def valid(path, expected):
    try:
        digest = hashlib.sha256()
        with (path / "bun").open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected
    except OSError:
        return False

def path_size(path):
    size = 0
    for parent, dirs, files in os.walk(path, topdown=True, followlinks=False):
        for name in files:
            try:
                size += os.lstat(os.path.join(parent, name)).st_size
            except OSError:
                pass
    return size

entries = []
for path in root.iterdir():
    match = re.fullmatch(r"bun-([0-9a-f]{64})", path.name)
    if not match or path.is_symlink() or not path.is_dir():
        continue
    try:
        entries.append((match.group(1), path.stat().st_mtime_ns, path))
    except OSError:
        continue
entries.sort(key=lambda item: (item[0] == protected_sha, item[1]), reverse=True)

if protected_sha:
    protected = next((item for item in entries if item[0] == protected_sha), None)
    if protected is None or not valid(protected[2], protected_sha):
        print(
            "claude update: warning: active Bun cache is missing or corrupt; no Bun cache removed",
            file=sys.stderr,
        )
        raise SystemExit(0)

retained = []
for entry in entries:
    if len(retained) >= keep:
        break
    if valid(entry[2], entry[0]):
        retained.append(entry)
retained_paths = {entry[2] for entry in retained}
victims = [entry for entry in entries if entry[2] not in retained_paths]
freed = 0
for _, _, path in victims:
    freed += path_size(path)
    shutil.rmtree(path)
if victims:
    print(
        f"claude update: Bun cache cleanup: {len(retained)} retained, "
        f"{len(victims)} removed ({freed / (1024 * 1024):.1f} MiB freed)"
    )
PY
}

run_cache_cleanup() {
  local root="$1"
  local protected_sha=""
  if [ "$#" -ge 2 ]; then protected_sha="$2"; fi
  if ! cleanup_claude_workdirs "$root" || ! prune_bun_cache "$root" "$protected_sha"; then
    echo "claude update: warning: cache cleanup failed; update remains installed" >&2
  fi
}

# Releases are vX.Y.Z, or vX.Y.Z-rN when the same Claude version was re-cut
# with a changed toolchain. Each tag points at the commit CI built it from.
release_url="$(curl -fsSIL --max-time 30 -o /dev/null -w '%{url_effective}' \
  https://github.com/wmdhs12138/claude-code-termux/releases/latest)"
if [[ "$release_url" =~ ^https://github\.com/wmdhs12138/claude-code-termux/releases/tag/(v([0-9]+\.[0-9]+\.[0-9]+)(-r[1-9][0-9]*)?)$ ]]; then
  release_tag="${BASH_REMATCH[1]}"
  latest="${BASH_REMATCH[2]}"
else
  echo "claude update: no valid, CI-approved Claude Release: $release_url" >&2
  exit 1
fi

# Prints the three input hashes and the output hash of a build manifest.
manifest_hashes() {
  local require_acceptance="$1"
  python3 -c '
import json
import re
import sys

try:
    doc = json.load(sys.stdin)
    bun = doc["base_bun"]
    if doc["claude"] != sys.argv[1]:
        raise ValueError("Claude version mismatch")
    values = (doc["claude_linux_arm64_sha256"],
              bun["archive_sha256"], bun["binary_sha256"], doc["output_sha256"])
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for value in values):
        raise ValueError("invalid SHA-256")
    smoke = doc["tui_smoke"]
    if smoke.get("ran") is not True or smoke.get("result") != "pass":
        raise ValueError("TUI smoke did not pass")
    if sys.argv[2] == "1":
        acceptance = doc["ci_acceptance"]
        if (acceptance.get("runtime") != "termux-docker/bionic"
                or acceptance.get("architecture") != "aarch64"
                or acceptance.get("version_probe") != "pass"
                or acceptance.get("tui_smoke") != "pass"):
            raise ValueError("Bionic acceptance did not pass")
except (KeyError, TypeError, ValueError) as exc:
    raise SystemExit(f"claude update: invalid build manifest: {exc}") from None
print(" ".join(values))
' "$latest" "$require_acceptance"
}

approved_hashes="$(curl -fsSL --max-time 30 \
  "https://github.com/wmdhs12138/claude-code-termux/releases/download/$release_tag/build-manifest.json" \
  | manifest_hashes 1)"
approved_output_sha="$(printf '%s\n' "$approved_hashes" | awk '{print $4}')"
current="$("$target" --version | awk '{print $1}')"
if ! [[ "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "claude update: invalid installed version '$current'" >&2
  exit 1
fi
current_sha="$(sha256sum "$target" | cut -d' ' -f1)"
version_order="$(python3 - "$current" "$latest" <<'PY'
import sys
current, approved = (tuple(map(int, value.split("."))) for value in sys.argv[1:])
print((current > approved) - (current < approved))
PY
)"

# The binary hash decides, not the version: a re-cut (vX.Y.Z-rN) keeps the
# version and changes the binary. Builds are reproducible, so building the
# approved tag here gives the bytes CI accepted.
if [ "$current_sha" = "$approved_output_sha" ]; then
  state="current"
elif [ "$version_order" = "1" ]; then
  state="newer"
else
  state="behind"
fi

if [ "$check" = "1" ]; then
  case "$state" in
    current) echo "Claude Code $current is up to date ($release_tag)" ;;
    newer) echo "Claude Code $current is newer than the latest approved release $latest ($release_tag)" ;;
    *)
      if [ "$version_order" = "0" ]; then
        echo "Update available: $release_tag is a different build of Claude Code $latest; run: claude update"
      else
        echo "Update available: Claude Code $current → $latest ($release_tag); run: claude update"
      fi
      ;;
  esac
  exit 0
fi
cache_root="$cache_base/claude-code-termux/self-update"
mkdir -p "$cache_root"
exec 7>"$cache_root/.update.lock"
if ! flock -n 7; then
  echo "claude update: another update is already running" >&2
  exit 1
fi
# The terminal gets a header, download progress, one status line and the
# result. Everything else -- toolchain build output, cache cleanup -- goes to
# this log, which the failure message points to.
log="$cache_root/update.log"
if [ "$force" != "1" ] && [ "$state" != "behind" ]; then
  if [ "$state" = "current" ]; then
    echo "Claude Code $current is up to date ($release_tag); use --force to rebuild"
  else
    echo "Claude Code $current is newer than the latest approved release $latest; use --force to rebuild $latest"
  fi
  active_bun_sha=""
  if [ -r "$cache_root/active-bun-sha256" ]; then
    read -r active_bun_sha < "$cache_root/active-bun-sha256" || active_bun_sha=""
    if ! [[ "$active_bun_sha" =~ ^[0-9a-f]{64}$ ]]; then active_bun_sha=""; fi
  fi
  run_cache_cleanup "$cache_root" "$active_bun_sha" >>"$log" 2>&1
  exit 0
fi

: > "$log"
fail() {
  echo "claude update: $1; the installed Claude Code is unchanged" >&2
  if [ -s "$log" ]; then
    local shown="$log"
    if [ -n "${HOME:-}" ]; then shown="${log/#"$HOME"/\~}"; fi
    echo "  last lines of $shown:" >&2
    tail -n 8 "$log" | sed 's/^/    /' >&2
  fi
  exit 1
}
if [ "$current" = "$latest" ]; then
  echo "Rebuilding Claude Code $latest from $release_tag"
else
  echo "Updating Claude Code $current → $latest ($release_tag)"
fi
started="$SECONDS"

cleanup_claude_workdirs "$cache_root" >>"$log" 2>&1

tmp_root="$(printenv TMPDIR 2>/dev/null || true)"
if [ -z "$tmp_root" ]; then tmp_root="/data/data/com.termux/files/usr/tmp"; fi
mkdir -p "$tmp_root"
stage="$(mktemp -d "$tmp_root/claude-code-termux-update.XXXXXX")"
replacement=""
marker_replacement=""
cleanup_update() {
  if [ -n "$replacement" ]; then rm -f "$replacement"; fi
  if [ -n "$marker_replacement" ]; then rm -f "$marker_replacement"; fi
  if [ -n "$stage" ]; then rm -rf "$stage"; fi
}
trap cleanup_update EXIT
source_dir="$stage/source"
tag_ref="refs/tags/$release_tag"
commit="$(git ls-remote --tags https://github.com/wmdhs12138/claude-code-termux.git \
  "$tag_ref" "$tag_ref^{}" 2>>"$log" | awk -v tag="$tag_ref" \
  '$2 == tag {commit = $1} $2 == tag "^{}" {commit = $1} END {print commit}')"
if ! [[ "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  fail "could not find the commit of $release_tag"
fi
echo "release $release_tag, toolchain $commit" >>"$log"
curl -fsSL --retry 3 --retry-all-errors \
  "https://github.com/wmdhs12138/claude-code-termux/archive/$commit.tar.gz" \
  -o "$stage/toolchain.tar.gz" 2>>"$log" || fail "could not download the toolchain"
mkdir "$source_dir"
tar -xzf "$stage/toolchain.tar.gz" -C "$source_dir" --strip-components=1 2>>"$log" \
  || fail "could not unpack the toolchain"

# fd 3 is the terminal: the build puts download bars and its status line there
# (CLAUDE_CODE_TERMUX_PROGRESS_FD) and everything else into the log.
if ! (cd "$source_dir" && \
      CLAUDE_CODE_TERMUX_PROGRESS_FD=3 \
      CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE="$cache_root/bun-bases" \
      bash scripts/build.sh "$latest") 3>&2 >>"$log" 2>&1; then
  fail "the build failed"
fi
candidate="$source_dir/dist/claude"
[ -x "$candidate" ] || fail "the build produced no binary"
candidate_version="$("$candidate" --version 2>>"$log" | awk '{print $1}')"
[ "$candidate_version" = "$latest" ] \
  || fail "the new binary reports version '$candidate_version', expected $latest"
built_hashes="$(manifest_hashes 0 < "$source_dir/dist/build-manifest.json" 2>>"$log")" \
  || fail "the build manifest is invalid"
if [ "${built_hashes% *}" != "${approved_hashes% *}" ]; then
  fail "the build inputs differ from the approved release"
fi
if [ "$built_hashes" != "$approved_hashes" ]; then
  fail "the binary built here differs from the one CI accepted for $release_tag"
fi
active_bun_sha="$(printf '%s\n' "$built_hashes" | awk '{print $3}')"
marker_replacement="$(mktemp "$cache_root/.active-bun-sha256.XXXXXX")"
printf '%s\n' "$active_bun_sha" > "$marker_replacement"

target_dir="$(dirname "$target")"
replacement="$(mktemp "$target_dir/.claude-update.XXXXXX")" \
  || fail "cannot write next to $target"
{ cp "$candidate" "$replacement" && chmod 755 "$replacement"; } 2>>"$log" \
  || fail "could not stage the new binary next to $target"
replacement_version="$("$replacement" --version 2>>"$log" | awk '{print $1}')"
[ "$replacement_version" = "$latest" ] || fail "the staged binary failed its version check"
mv -f "$replacement" "$target" 2>>"$log" || fail "could not replace $target"
replacement=""
mv -f "$marker_replacement" "$cache_root/active-bun-sha256"
marker_replacement=""
rm -rf "$stage"
stage=""
run_cache_cleanup "$cache_root" "$active_bun_sha" >>"$log" 2>&1

elapsed=$((SECONDS - started))
if [ "$elapsed" -ge 60 ]; then took="$((elapsed / 60)) min $((elapsed % 60)) s"; else took="$elapsed s"; fi
if [ "$current" = "$latest" ]; then
  echo "Rebuilt Claude Code $latest in $took"
else
  echo "Updated Claude Code $current → $latest in $took"
fi
