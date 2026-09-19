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

prune_update_cache() {
  local root="$1"
  local protected_name=""
  local keep
  if [ "$#" -ge 2 ]; then protected_name="$2"; fi
  keep="$(printenv CLAUDE_CODE_TERMUX_CACHE_KEEP 2>/dev/null || printf 2)"
  if ! [[ "$keep" =~ ^[0-9]+$ ]] || [ "$keep" -lt 2 ]; then
    echo "claude update: invalid cache retention '$keep'; keeping 2" >&2
    keep=2
  fi
  [ -d "$root" ] || return 0
  python3 - "$root" "$protected_name" "$keep" <<'PY'
import os
from pathlib import Path
import json
import re
import shutil
import sys

root = Path(sys.argv[1])
protected_name = sys.argv[2]
keep = int(sys.argv[3])
legacy_pattern = re.compile(r"toolchain-[0-9a-f]{40}")
versioned_pattern = re.compile(r"claude-(\d+\.\d+\.\d+)-toolchain-[0-9a-f]{40}")
version_pattern = re.compile(r"\d+\.\d+\.\d+")

entries = []
for path in root.iterdir():
    match = versioned_pattern.fullmatch(path.name)
    if not (match or legacy_pattern.fullmatch(path.name)) or path.is_symlink() or not path.is_dir():
        continue
    try:
        manifest = json.loads((path / "dist" / "build-manifest.json").read_text())
        version = manifest["claude"]
        if not isinstance(version, str) or not version_pattern.fullmatch(version):
            continue
        if match and match.group(1) != version:
            continue
        mtime = path.stat().st_mtime_ns
    except (OSError, ValueError, KeyError, TypeError):
        continue
    entries.append((tuple(map(int, version.split("."))), path.name == protected_name, mtime, path))

entries.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
retained_versions = set()
for entry in entries:
    if entry[1]:
        retained_versions.add(entry[0])
for entry in entries:
    if len(retained_versions) >= keep:
        break
    retained_versions.add(entry[0])
# Keep one cache for each of the newest distinct Claude versions. A forced
# rebuild may leave both a legacy and a versioned cache for the same version;
# prefer the cache used by this update, then the most recently modified one.
retained = []
victims = []
for entry in entries:
    version = entry[0]
    if version in retained_versions and not any(item[0] == version for item in retained):
        retained.append(entry)
    else:
        victims.append(entry)
if not victims:
    print(f"claude update: cache cleanup: {len(retained)} versions retained, 0 removed")
    raise SystemExit(0)

freed = 0
for _, _, _, path in victims:
    for parent, dirs, files in os.walk(path, topdown=True, followlinks=False):
        for name in files:
            try:
                freed += os.lstat(os.path.join(parent, name)).st_size
            except OSError:
                pass
        for name in dirs:
            child = os.path.join(parent, name)
            if os.path.islink(child):
                try:
                    freed += os.lstat(child).st_size
                except OSError:
                    pass
    shutil.rmtree(path)

print(
    f"claude update: cache cleanup: {len(retained)} versions retained, "
    f"{len(victims)} removed ({freed / (1024 * 1024):.1f} MiB freed)"
)
PY
}

run_cache_cleanup() {
  local protected_name=""
  if [ "$#" -ge 2 ]; then protected_name="$2"; fi
  if ! prune_update_cache "$1" "$protected_name"; then
    echo "claude update: warning: cache cleanup failed; update remains installed" >&2
  fi
}

latest="$(curl -fsSL --max-time 30 https://downloads.claude.ai/claude-code-releases/latest)"
case "$latest" in
  ''|*[!0-9.]*) echo "claude update: invalid latest version '$latest'" >&2; exit 1 ;;
esac
current="$($target --version | awk '{print $1}')"

if [ "$check" = "1" ]; then
  printf 'current: %s\nlatest:  %s\n' "$current" "$latest"
  if [ "$current" = "$latest" ]; then
    echo "Claude Code is up to date"
  else
    echo "Claude Code update available"
  fi
  exit 0
fi
cache_root="$cache_base/claude-code-termux/self-update"
if [ "$force" != "1" ] && [ "$current" = "$latest" ]; then
  echo "Claude Code $current is already up to date (use --force to rebuild)"
  run_cache_cleanup "$cache_root"
  exit 0
fi

mkdir -p "$cache_root"
commit="$(curl -fsSL --max-time 30 https://api.github.com/repos/wmdhs12138/claude-code-termux/commits/main \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')"
case "$commit" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
  *) echo "claude update: invalid toolchain commit '$commit'" >&2; exit 1 ;;
esac
source_dir="$cache_root/claude-$latest-toolchain-$commit"
echo "claude update: $current -> $latest"
echo "claude update: toolchain $commit"
if [ ! -r "$source_dir/scripts/build.sh" ]; then
  stage="$(mktemp -d "$cache_root/download.XXXXXX")"
  trap 'rm -rf "$stage"' EXIT
  echo "claude update: downloading toolchain..."
  curl -fsSL --retry 3 --retry-all-errors \
    "https://github.com/wmdhs12138/claude-code-termux/archive/$commit.tar.gz" \
    -o "$stage/toolchain.tar.gz"
  mkdir "$stage/source"
  tar -xzf "$stage/toolchain.tar.gz" -C "$stage/source" --strip-components=1
  mv "$stage/source" "$source_dir"
  rm -rf "$stage"
  trap - EXIT
fi

(cd "$source_dir" && \
  CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE="$cache_root/bun-bases" \
  bash scripts/build.sh "$latest")
candidate="$source_dir/dist/claude"
test -x "$candidate"
candidate_version="$($candidate --version | awk '{print $1}')"
test "$candidate_version" = "$latest"

target_dir="$(dirname "$target")"
replacement="$(mktemp "$target_dir/.claude-update.XXXXXX")"
trap 'rm -f "$replacement"' EXIT
cp "$candidate" "$replacement"
chmod 755 "$replacement"
replacement_version="$($replacement --version | awk '{print $1}')"
test "$replacement_version" = "$latest"
mv -f "$replacement" "$target"
trap - EXIT
echo "Claude Code updated successfully: $current -> $latest"
touch "$source_dir"
run_cache_cleanup "$cache_root" "$(basename "$source_dir")"
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
