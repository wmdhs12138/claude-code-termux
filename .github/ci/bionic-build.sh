#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${CLAUDE_VERSION_INPUT:-latest}"

echo "::group::Install Termux build dependencies"
pkg update -y
pkg install -y \
  bash binutils coreutils curl file gawk git make python ripgrep sed tar unzip \
  util-linux
echo "::endgroup::"

cd "$ROOT"
# The separate tests job is a hard dependency of this job. Do not repeat those
# host-side fixture tests here: several intentionally create conventional
# /bin/sh and /usr/bin/env scripts, paths that a real Termux filesystem does
# not provide. This container is reserved for the checks that need Bionic.
SMOKE_SECONDS="${SMOKE_SECONDS:-10}" make build VERSION="$VERSION"

ACTUAL_VERSION="$(python3 -c 'import json; print(json.load(open("dist/build-manifest.json"))["claude"])')"
test "$ACTUAL_VERSION" = "$(tr -d '[:space:]' < work/.claude-version)"
./dist/claude --version | grep -F "$ACTUAL_VERSION" >/dev/null

python3 - <<'PY'
import json
import os
import platform

with open("dist/build-manifest.json") as f:
    doc = json.load(f)
smoke = doc.get("tui_smoke") or {}
assert smoke.get("ran") is True, doc
assert smoke.get("result") == "pass", doc
assert doc.get("output_version", "").startswith(doc["claude"]), doc
doc["ci_acceptance"] = {
    "runtime": "termux-docker/bionic",
    "termux_docker": os.environ.get("TERMUX_DOCKER_IMAGE", "unknown"),
    "architecture": platform.machine(),
    "version_probe": "pass",
    "tui_smoke": "pass",
}
with open("dist/build-manifest.json", "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PY

{
  printf 'claude=%s\n' "$ACTUAL_VERSION"
  printf 'target=android-aarch64\n'
  printf 'runtime=bionic\n'
  printf 'architecture=%s\n' "$(uname -m)"
  printf 'termux_docker=%s\n' "${TERMUX_DOCKER_IMAGE:-unknown}"
  printf 'android_api=%s\n' "$(getprop ro.build.version.sdk 2>/dev/null || printf unknown)"
  printf 'android_release=%s\n' "$(getprop ro.build.version.release 2>/dev/null || printf unknown)"
  printf 'bun=%s\n' "$(python3 -c 'import json; print(json.load(open("dist/build-manifest.json"))["base_bun"]["version"])')"
} > "$ROOT/work/bionic-ci.txt"

printf 'bionic-build: OK: Claude Code %s executed and rendered in Termux Bionic\n' \
  "$ACTUAL_VERSION"
