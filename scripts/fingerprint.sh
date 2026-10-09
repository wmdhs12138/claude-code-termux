#!/usr/bin/env bash
# Toolchain fingerprint: the 7 hex digits of a toolchain-v<N>-<fingerprint> tag.
#
#   scripts/fingerprint.sh          print the fingerprint
#   scripts/fingerprint.sh --paths  print the pathspecs it covers
#
# sha256 over the sorted per-file sha256 of every tracked file under the paths
# below. git ls-files, not find: stray untracked files (__pycache__ and
# friends) would make the same commit hash differently on a machine that had
# run the tools. CI cuts a toolchain release whenever this number is new, and
# `claude update` builds from the newest accepted one, so everything the build
# reads must be covered -- runtime/ and the Bun pin in versions.json included.
# .github/ is covered because it decides what a release says.
set -euo pipefail
cd "$(dirname "$0")/.."
PATHS=(install.sh versions.json scripts tools runtime .github)
if [ "${1:-}" = "--paths" ]; then
  echo "${PATHS[*]}"
  exit 0
fi
git ls-files -z -- "${PATHS[@]}" | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-7
