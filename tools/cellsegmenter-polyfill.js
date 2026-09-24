// Pure-JS implementation of the Bun.ant.CellSegmenter ABI that Claude Code
// >= 2.1.271 expects from Anthropic's private @anthropic-ai/bun-internal
// runtime. The grafted Android Bun has no Bun.ant, so the Ink renderer throws
// "This build of @anthropic-ai/bun-internal has no Bun.ant.CellSegmenter" and
// the first frame never completes (blank terminal).
//
// tools/embed_preload.py inserts this file into the compiled standalone entry
// module, before Claude's source runs. Implemented surface
// (discovered from 2.1.272's src/ink call sites):
//   new CellSegmenter({ambiguousIsNarrow, substitute, screen})
//     .graphemes .sgrKeys .sgrCloseKeys .uris   append-only pools
//     .segment(text, cells, runs, reordered)    -> cell count or -needed
//     .paint(screenCells, width, x, y, lineCells, count, _, charIndices, words)
//     .setCell(screenCells, width, x, y, charIndex, packedStyle)
// Line cells pack (run << 10) | (tab ? 256 : 0) | columnWidth, screen cells
// pack styleId << 17 | linkId << 2 | widthCategory, matching src/ink readers.

(function () {
  if (typeof Bun === "undefined") return;
  // A directly-executed Android standalone has no launcher to provide these
  // safety defaults. Preserve explicit user choices, but otherwise avoid the
  // embedded Linux/glibc ripgrep and the updater that would replace this
  // bionic executable with an official glibc build.
  if (typeof process !== "undefined" && process.platform === "android") {
    if (process.env.USE_BUILTIN_RIPGREP === undefined) process.env.USE_BUILTIN_RIPGREP = "0";
    if (process.env.DISABLE_AUTOUPDATER === undefined) process.env.DISABLE_AUTOUPDATER = "1";

    // Intercept the official updater before Claude's CLI sees it. The official
    // download is a glibc standalone, so updating a bionic graft means building
    // a new graft locally and atomically replacing this executable.
    var argv = process.argv || [];
    var updateIndex = -1;
    for (var ai = 1; ai < argv.length && ai <= 2; ai++) {
      if (argv[ai] === "update" || argv[ai] === "upgrade") {
        updateIndex = ai;
        break;
      }
    }
    if (updateIndex !== -1) {
      var updateArgs = argv.slice(updateIndex + 1);
      var updateCheck = updateArgs.indexOf("--check") !== -1;
      var updateForce = updateArgs.indexOf("--force") !== -1;
      var updateScript = String.raw`
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

release_url="$(curl -fsSIL --max-time 30 -o /dev/null -w '%{url_effective}' \
  https://github.com/wmdhs12138/claude-code-termux/releases/latest)"
if [[ "$release_url" =~ ^https://github\.com/wmdhs12138/claude-code-termux/releases/tag/(v([0-9]+\.[0-9]+\.[0-9]+)(-r[1-9][0-9]*)?)$ ]]; then
  release_tag="$(basename "$release_url")"
  latest="$(printf '%s' "$release_tag" | sed -E 's/^v([0-9]+\.[0-9]+\.[0-9]+)(-r[1-9][0-9]*)?$/\1/')"
else
  echo "claude update: no valid, CI-approved Claude Release: $release_url" >&2
  exit 1
fi

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
              bun["archive_sha256"], bun["binary_sha256"])
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for value in values):
        raise ValueError("invalid input SHA-256")
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
current="$($target --version | awk '{print $1}')"
if ! [[ "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "claude update: invalid installed version '$current'" >&2
  exit 1
fi
version_order="$(python3 - "$current" "$latest" <<'PY'
import sys
current, approved = (tuple(map(int, value.split("."))) for value in sys.argv[1:])
print((current > approved) - (current < approved))
PY
)"

if [ "$check" = "1" ]; then
  printf 'current: %s\nlatest approved: %s (%s)\n' "$current" "$latest" "$release_tag"
  if [ "$version_order" = "0" ]; then
    echo "Claude Code is up to date"
  elif [ "$version_order" = "1" ]; then
    echo "Installed version is newer than the latest approved Release"
  else
    echo "Claude Code update available"
  fi
  exit 0
fi
cache_root="$cache_base/claude-code-termux/self-update"
mkdir -p "$cache_root"
exec 7>"$cache_root/.update.lock"
if ! flock -n 7; then
  echo "claude update: another update is already running" >&2
  exit 1
fi
if [ "$force" != "1" ] && [ "$version_order" != "-1" ]; then
  if [ "$version_order" = "0" ]; then
    echo "Claude Code $current is already up to date (use --force to rebuild)"
  else
    echo "Claude Code $current is newer than the latest approved Release $latest; use --force to rebuild $latest"
  fi
  active_bun_sha=""
  if [ -r "$cache_root/active-bun-sha256" ]; then
    read -r active_bun_sha < "$cache_root/active-bun-sha256" || active_bun_sha=""
    if ! [[ "$active_bun_sha" =~ ^[0-9a-f]{64}$ ]]; then active_bun_sha=""; fi
  fi
  run_cache_cleanup "$cache_root" "$active_bun_sha"
  exit 0
fi

cleanup_claude_workdirs "$cache_root"

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
source_tag="$release_tag"
expected_hashes="$approved_hashes"
approved_source_sha="$(printf '%s\n' "$approved_hashes" | awk '{print $1}')"
toolchain_tag="$(git ls-remote --refs --tags https://github.com/wmdhs12138/claude-code-termux.git \
  'refs/tags/toolchain-v*' | sed -n 's|.*refs/tags/\(toolchain-v[0-9]\+-[0-9a-f]\{7\}\)$|\1|p' \
  | sort -V | tail -1)"
if [ -n "$toolchain_tag" ]; then
  if toolchain_hashes="$(curl -fsSL --max-time 30 \
    "https://github.com/wmdhs12138/claude-code-termux/releases/download/$toolchain_tag/build-manifest.json" \
    2>/dev/null | manifest_hashes 1 2>/dev/null)" \
    && [ "$(printf '%s\n' "$toolchain_hashes" | awk '{print $1}')" = "$approved_source_sha" ]; then
      source_tag="$toolchain_tag"
      expected_hashes="$toolchain_hashes"
  fi
fi
tag_ref="refs/tags/$source_tag"
commit="$(git ls-remote --tags https://github.com/wmdhs12138/claude-code-termux.git \
  "$tag_ref" "$tag_ref^{}" | awk -v tag="$tag_ref" \
  '$2 == tag {commit = $1} $2 == tag "^{}" {commit = $1} END {print commit}')"
if ! [[ "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "claude update: invalid commit for approved tag '$source_tag'" >&2
  exit 1
fi
echo "claude update: $current -> $latest (approved $release_tag)"
echo "claude update: toolchain $source_tag ($commit)"
echo "claude update: downloading toolchain..."
curl -fsSL --retry 3 --retry-all-errors \
  "https://github.com/wmdhs12138/claude-code-termux/archive/$commit.tar.gz" \
  -o "$stage/toolchain.tar.gz"
mkdir "$source_dir"
tar -xzf "$stage/toolchain.tar.gz" -C "$source_dir" --strip-components=1

(cd "$source_dir" && \
  CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE="$cache_root/bun-bases" \
  bash scripts/build.sh "$latest")
candidate="$source_dir/dist/claude"
test -x "$candidate"
candidate_version="$($candidate --version | awk '{print $1}')"
test "$candidate_version" = "$latest"
built_hashes="$(manifest_hashes 0 < "$source_dir/dist/build-manifest.json")"
if [ "$built_hashes" != "$expected_hashes" ]; then
  echo "claude update: built inputs differ from approved Release; keeping current Claude" >&2
  exit 1
fi
active_bun_sha="$(printf '%s\n' "$built_hashes" | awk '{print $3}')"
marker_replacement="$(mktemp "$cache_root/.active-bun-sha256.XXXXXX")"
printf '%s\n' "$active_bun_sha" > "$marker_replacement"

target_dir="$(dirname "$target")"
replacement="$(mktemp "$target_dir/.claude-update.XXXXXX")"
cp "$candidate" "$replacement"
chmod 755 "$replacement"
replacement_version="$($replacement --version | awk '{print $1}')"
test "$replacement_version" = "$latest"
mv -f "$replacement" "$target"
replacement=""
mv -f "$marker_replacement" "$cache_root/active-bun-sha256"
marker_replacement=""
echo "Claude Code updated successfully: $current -> $latest"
rm -rf "$stage"
stage=""
run_cache_cleanup "$cache_root" "$active_bun_sha"
`;
      var updateResult = Bun.spawnSync({
        cmd: [
          "bash",
          "-c",
          updateScript,
          "claude-self-update",
          process.execPath,
          updateForce ? "1" : "0",
          updateCheck ? "1" : "0",
          process.env.XDG_CACHE_HOME || (process.env.HOME ? process.env.HOME + "/.cache" : "/data/local/tmp"),
        ],
        stdin: "inherit",
        stdout: "inherit",
        stderr: "inherit",
        env: process.env,
      });
      process.exit(updateResult.exitCode === 0 ? 0 : updateResult.exitCode || 1);
    }
  }
  if (!Bun.ant) {
    try {
      Bun.ant = {};
    } catch {
      return;
    }
  }
  if (typeof Bun.ant.CellSegmenter === "function") return;

  var ESC = "\x1b";
  var DEFAULT_TAB_WIDTH = 8;
  var SUBSTITUTE = "\ufffd";
  var STYLE_ORDER = ["bold", "dim", "italic", "underline", "inverse", "strike", "fg", "bg"];
  var SGR_END = {
    bold: ESC + "[22m",
    dim: ESC + "[22m",
    italic: ESC + "[23m",
    underline: ESC + "[24m",
    inverse: ESC + "[27m",
    strike: ESC + "[29m",
    fg: ESC + "[39m",
    bg: ESC + "[49m",
  };

  function displayWidth(text, ambiguousIsNarrow) {
    if (text === "" || text === "\t" || text === "\n" || text === "\r") return 0;
    if (typeof Bun.stringWidth === "function") {
      try {
        var width = Bun.stringWidth(text, { ambiguousIsNarrow: !!ambiguousIsNarrow });
        if (width > 0) return width > 2 ? 2 : width;
        if (width === 0) return 0;
      } catch {}
    }
    var total = 0;
    for (var i = 0; i < text.length; ) {
      var cp = text.codePointAt(i);
      i += cp > 0xffff ? 2 : 1;
      if (cp < 32 || (cp >= 0x7f && cp < 0xa0)) continue;
      if (
        (cp >= 0x1100 && cp <= 0x115f) ||
        (cp >= 0x2e80 && cp <= 0xa4cf) ||
        (cp >= 0xac00 && cp <= 0xd7a3) ||
        (cp >= 0xf900 && cp <= 0xfaff) ||
        (cp >= 0xfe30 && cp <= 0xfe6f) ||
        (cp >= 0xff00 && cp <= 0xff60) ||
        (cp >= 0xffe0 && cp <= 0xffe6) ||
        (cp >= 0x1f300 && cp <= 0x1faff) ||
        (cp >= 0x20000 && cp <= 0x3fffd)
      )
        total += 2;
      else total += 1;
    }
    return total > 2 ? 2 : total;
  }

  function applySgr(params, state) {
    if (params.length === 0) params = [0];
    for (var i = 0; i < params.length; i++) {
      var p = params[i];
      if (p === 0) {
        state.clear();
      } else if (p === 1 || p === 2 || p === 3 || p === 4 || p === 7 || p === 9) {
        var kind = { 1: "bold", 2: "dim", 3: "italic", 4: "underline", 7: "inverse", 9: "strike" }[p];
        state.set(kind, { code: ESC + "[" + p + "m", endCode: SGR_END[kind] });
      } else if (p === 22) {
        state.delete("bold");
        state.delete("dim");
      } else if (p === 23 || p === 24 || p === 27 || p === 29) {
        state.delete({ 23: "italic", 24: "underline", 27: "inverse", 29: "strike" }[p]);
      } else if ((p >= 30 && p <= 37) || (p >= 90 && p <= 97)) {
        state.set("fg", { code: ESC + "[" + p + "m", endCode: SGR_END.fg });
      } else if ((p >= 40 && p <= 47) || (p >= 100 && p <= 107)) {
        state.set("bg", { code: ESC + "[" + p + "m", endCode: SGR_END.bg });
      } else if (p === 39) {
        state.delete("fg");
      } else if (p === 49) {
        state.delete("bg");
      } else if (p === 38 || p === 48) {
        var colorKind = p === 38 ? "fg" : "bg";
        if (params[i + 1] === 5 && Number.isInteger(params[i + 2])) {
          state.set(colorKind, {
            code: ESC + "[" + p + ";5;" + params[i + 2] + "m",
            endCode: SGR_END[colorKind],
          });
          i += 2;
        } else if (
          params[i + 1] === 2 &&
          Number.isInteger(params[i + 2]) &&
          Number.isInteger(params[i + 3]) &&
          Number.isInteger(params[i + 4])
        ) {
          state.set(colorKind, {
            code:
              ESC + "[" + p + ";2;" + params[i + 2] + ";" + params[i + 3] + ";" + params[i + 4] + "m",
            endCode: SGR_END[colorKind],
          });
          i += 4;
        }
      }
    }
  }

  function parseCsiParams(body) {
    if (body === "") return [];
    return body.split(";").map(function (part) {
      var n = parseInt(part, 10);
      return Number.isInteger(n) ? n : 0;
    });
  }

  function* scan(text) {
    var i = 0;
    var start = 0;
    var n = text.length;
    while (i < n) {
      var code = text.charCodeAt(i);
      if (code !== 0x1b && code !== 0x9b) {
        i++;
        continue;
      }
      if (i > start) yield { type: "text", value: text.slice(start, i) };
      if (code === 0x9b) {
        var csiEnd = i + 1;
        while (csiEnd < n && !(text.charCodeAt(csiEnd) >= 0x40 && text.charCodeAt(csiEnd) <= 0x7e)) csiEnd++;
        if (text[csiEnd] === "m") yield { type: "sgr", params: parseCsiParams(text.slice(i + 1, csiEnd)) };
        i = csiEnd + 1;
      } else {
        var next = text.charCodeAt(i + 1);
        if (next === 0x5b) {
          var end = i + 2;
          while (end < n && !(text.charCodeAt(end) >= 0x40 && text.charCodeAt(end) <= 0x7e)) end++;
          if (text[end] === "m") yield { type: "sgr", params: parseCsiParams(text.slice(i + 2, end)) };
          i = end + 1;
        } else if (next === 0x5d) {
          var oscEnd = i + 2;
          while (oscEnd < n) {
            if (text.charCodeAt(oscEnd) === 0x07) {
              oscEnd++;
              break;
            }
            if (text.charCodeAt(oscEnd) === 0x1b && text.charCodeAt(oscEnd + 1) === 0x5c) {
              oscEnd += 2;
              break;
            }
            oscEnd++;
          }
          var oscBody = text.slice(i + 2, oscEnd).replace(/\x07$|\x1b\\$/, "");
          if (oscBody.indexOf("8;") === 0) {
            var parts = oscBody.split(";");
            var uri = parts.slice(2).join(";");
            yield { type: "link", uri: uri === "" ? null : uri };
          }
          i = oscEnd;
        } else if (next === 0x28 || next === 0x29 || next === 0x2a || next === 0x2b || next === 0x23) {
          i += 3;
        } else if (!Number.isNaN(next)) {
          i += 2;
        } else {
          i++;
        }
      }
      start = i;
    }
    if (i > start) yield { type: "text", value: text.slice(start, i) };
  }

  class CellSegmenter {
    constructor(options) {
      options = options || {};
      this.screen = options.screen || {};
      this.ambiguousIsNarrow = !!options.ambiguousIsNarrow;
      this.tabWidth = this.screen.tabWidth || DEFAULT_TAB_WIDTH;
      this.emptyCharIndex = Number.isInteger(this.screen.emptyCharIndex) ? this.screen.emptyCharIndex : 0;
      this.spacerCharIndex = Number.isInteger(this.screen.spacerCharIndex) ? this.screen.spacerCharIndex : 1;
      this.substitute = Array.isArray(options.substitute) ? options.substitute : [];
      this.graphemes = [];
      this.sgrKeys = [""];
      this.sgrCloseKeys = [""];
      this.uris = [""];
      this._segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
      this._styleByKey = new Map();
      this._state = new Map();
      this._uriById = new Map();
    }

    _isSubstitute(text) {
      for (var i = 0; i < text.length; ) {
        var cp = text.codePointAt(i);
        i += cp > 0xffff ? 2 : 1;
        for (var r = 0; r < this.substitute.length; r++) {
          if (cp >= this.substitute[r][0] && cp <= this.substitute[r][1]) return true;
        }
      }
      return false;
    }

    _styleIndex(state) {
      var codes = [];
      var closes = [];
      for (var i = 0; i < STYLE_ORDER.length; i++) {
        var component = state.get(STYLE_ORDER[i]);
        if (component) {
          codes.push(component.code);
          closes.push(component.endCode);
        }
      }
      if (codes.length === 0) return 0;
      var key = codes.join("\u0001");
      var index = this._styleByKey.get(key);
      if (index === undefined) {
        index = this.sgrKeys.length;
        this.sgrKeys.push(codes.join("\x00"));
        this.sgrCloseKeys.push(closes.join("\x00"));
        this._styleByKey.set(key, index);
      }
      return index;
    }

    _uriIndex(uri) {
      var index = this._uriById.get(uri);
      if (index === undefined) {
        index = this.uris.length;
        this.uris.push(uri);
        this._uriById.set(uri, index);
      }
      return index;
    }

    segment(text, cells, runs, reordered) {
      var count = 0;
      var style = 0;
      var link = 0;
      var run = 0;
      var cellRun = 0;
      var runStyle = -1;
      var runLink = -1;
      var state = this._state;
      state.clear();
      var capacity = cells.length >> 1;
      for (var token of scan(text)) {
        if (token.type === "sgr") {
          applySgr(token.params, state);
          style = this._styleIndex(state);
          continue;
        }
        if (token.type === "link") {
          link = token.uri === null ? 0 : this._uriIndex(token.uri);
          continue;
        }
        for (var part of this._segmenter.segment(token.value)) {
          var grapheme = part.segment;
          var width;
          var tab = false;
          if (grapheme === "\t") {
            tab = true;
            width = 0;
          } else {
            if (this._isSubstitute(grapheme)) grapheme = SUBSTITUTE;
            width = displayWidth(grapheme, this.ambiguousIsNarrow);
            if (width <= 0) continue;
          }
          // src/ink retries exactly once and keeps the new count even if the
          // retry also returns negative, so report an upper bound that always
          // fits: cells never outnumber input characters.
          if (count >= capacity) return -(capacity + text.length);
          if (style !== runStyle || link !== runLink) {
            runStyle = style;
            runLink = link;
            cellRun = run;
            runs[run * 2] = style;
            runs[run * 2 + 1] = link;
            run++;
          }
          cells[count * 2] = this.graphemes.length;
          cells[count * 2 + 1] = (cellRun << 10) | (tab ? 256 : 0) | width;
          this.graphemes.push(grapheme);
          count++;
        }
      }
      return count;
    }

    paint(cells, width, x0, y, lineCells, count, _unused, charIndices, words) {
      var tabWidth = this.tabWidth;
      var spacer = this.spacerCharIndex;
      var x = x0;
      var min = -1;
      var rowBase = (y * width) << 1;
      for (var i = 0; i < count; i++) {
        var packed = lineCells[i * 2 + 1];
        var word = words[packed >>> 10] || 0;
        var style = word >>> 17;
        var link = (word >>> 2) & 32767;
        var styleBits = (style << 17) | (link << 2);
        if (packed & 256) {
          var spaces = tabWidth - (((x % tabWidth) + tabWidth) % tabWidth);
          while (spaces-- > 0 && x < width) {
            var tabIndex = rowBase + (x << 1);
            cells[tabIndex] = this.emptyCharIndex;
            cells[tabIndex + 1] = styleBits;
            if (min < 0) min = x;
            x++;
          }
          continue;
        }
        var cellWidth = packed & 255;
        if (cellWidth <= 0) continue;
        if (x + cellWidth > width) break;
        var index = rowBase + (x << 1);
        var char = charIndices[lineCells[i * 2]];
        cells[index] = char === undefined ? this.emptyCharIndex : char;
        cells[index + 1] = styleBits | (cellWidth === 2 ? 1 : 0);
        if (min < 0) min = x;
        if (cellWidth === 2 && x + 1 < width) {
          cells[index + 2] = spacer;
          cells[index + 3] = styleBits | 2;
        }
        x += cellWidth;
      }
      if (min < 0) min = x0;
      return x * 68719476736 + min * 1048576 + x;
    }

    setCell(cells, width, x, y, charCode, packed) {
      if (x < 0 || y < 0 || x >= width) return 0;
      var index = ((y * width + x) << 1);
      cells[index] = charCode;
      cells[index + 1] = packed;
      var span = (packed & 3) === 1 ? 2 : 1;
      if (span === 2 && x + 1 < width) {
        cells[index + 2] = this.spacerCharIndex;
        cells[index + 3] = (packed & ~3) | 2;
      }
      return (x + span) * 68719476736 + x * 1048576 + (x + span);
    }
  }

  Bun.ant.CellSegmenter = CellSegmenter;
})();
