#!/usr/bin/env bash
# Build and install the latest Bionic-accepted Claude Release from its tagged toolchain.
set -euo pipefail

repo_url="https://github.com/wmdhs12138/claude-code-termux"
git_url="$repo_url.git"
release_url="$(curl -fsSIL --max-time 30 -o /dev/null -w '%{url_effective}' \
  "$repo_url/releases/latest")"
if [[ "$release_url" =~ ^https://github\.com/wmdhs12138/claude-code-termux/releases/tag/(v([0-9]+\.[0-9]+\.[0-9]+)(-r[1-9][0-9]*)?)$ ]]; then
  release_tag="${BASH_REMATCH[1]}"
  version="${BASH_REMATCH[2]}"
else
  echo "install: no valid, CI-approved Claude Release: $release_url" >&2
  exit 1
fi

manifest_hashes() {
  local path="$1"
  local require_acceptance="$2"
  python3 - "$path" "$version" "$require_acceptance" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1]) as handle:
        doc = json.load(handle)
    bun = doc["base_bun"]
    if doc["claude"] != sys.argv[2]:
        raise ValueError("Claude version mismatch")
    values = (doc["claude_linux_arm64_sha256"],
              bun["archive_sha256"], bun["binary_sha256"])
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for value in values):
        raise ValueError("invalid input SHA-256")
    smoke = doc["tui_smoke"]
    if smoke.get("ran") is not True or smoke.get("result") != "pass":
        raise ValueError("TUI smoke did not pass")
    if sys.argv[3] == "1":
        acceptance = doc["ci_acceptance"]
        if (acceptance.get("runtime") != "termux-docker/bionic"
                or acceptance.get("architecture") != "aarch64"
                or acceptance.get("version_probe") != "pass"
                or acceptance.get("tui_smoke") != "pass"):
            raise ValueError("Bionic acceptance did not pass")
except (OSError, KeyError, TypeError, ValueError) as exc:
    raise SystemExit(f"install: invalid build manifest: {exc}") from None
print(" ".join(values))
PY
}

cache_base="${XDG_CACHE_HOME:-$HOME/.cache}"
cache_root="$cache_base/claude-code-termux/self-update"
mkdir -p "$cache_root"
exec 7>"$cache_root/.update.lock"
if ! flock -n 7; then
  echo "install: another Claude install or update is already running" >&2
  exit 1
fi

tmp_root="${TMPDIR:-/data/data/com.termux/files/usr/tmp}"
mkdir -p "$tmp_root"
stage="$(mktemp -d "$tmp_root/claude-code-termux-install.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

curl -fsSL --max-time 30 \
  "$repo_url/releases/download/$release_tag/build-manifest.json" \
  -o "$stage/approved-manifest.json"
approved_hashes="$(manifest_hashes "$stage/approved-manifest.json" 1)"
approved_source_sha="$(printf '%s\n' "$approved_hashes" | awk '{print $1}')"
source_tag="$release_tag"
expected_hashes="$approved_hashes"

# A toolchain-only Release can carry a newer, separately accepted build of the
# same official Claude binary. If its evidence is unavailable, use the Claude tag.
toolchain_tag=""
if toolchain_refs="$(git ls-remote --refs --tags "$git_url" 'refs/tags/toolchain-v*')"; then
  toolchain_tag="$(printf '%s\n' "$toolchain_refs" \
    | sed -n 's|.*refs/tags/\(toolchain-v[0-9]\+-[0-9a-f]\{7\}\)$|\1|p' \
    | sort -V | tail -1)"
fi
if [ -n "$toolchain_tag" ]; then
  if curl -fsSL --max-time 30 \
    "$repo_url/releases/download/$toolchain_tag/build-manifest.json" \
    -o "$stage/toolchain-manifest.json" 2>/dev/null; then
    if toolchain_hashes="$(manifest_hashes "$stage/toolchain-manifest.json" 1 2>/dev/null)" \
      && [ "$(printf '%s\n' "$toolchain_hashes" | awk '{print $1}')" = "$approved_source_sha" ]; then
      source_tag="$toolchain_tag"
      expected_hashes="$toolchain_hashes"
    fi
  fi
fi

tag_ref="refs/tags/$source_tag"
commit="$(git ls-remote --tags "$git_url" "$tag_ref" "$tag_ref^{}" \
  | awk -v tag="$tag_ref" \
  '$2 == tag {commit = $1} $2 == tag "^{}" {commit = $1} END {print commit}')"
if ! [[ "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "install: invalid commit for approved tag '$source_tag'" >&2
  exit 1
fi

echo "install: Claude Code $version (approved $release_tag)" >&2
echo "install: toolchain $source_tag ($commit)" >&2
curl -fsSL --retry 3 --retry-all-errors "$repo_url/archive/$commit.tar.gz" \
  -o "$stage/toolchain.tar.gz"
source_dir="$stage/source"
mkdir "$source_dir"
tar -xzf "$stage/toolchain.tar.gz" -C "$source_dir" --strip-components=1

(cd "$source_dir" && \
  CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE="$cache_root/bun-bases" \
  bash scripts/build.sh "$version")
built_hashes="$(manifest_hashes "$source_dir/dist/build-manifest.json" 0)"
if [ "$built_hashes" != "$expected_hashes" ]; then
  echo "install: built inputs differ from approved Release; no Claude installed" >&2
  exit 1
fi
bash "$source_dir/install.sh" --no-build
