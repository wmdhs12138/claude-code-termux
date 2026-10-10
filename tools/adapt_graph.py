#!/usr/bin/env python3
"""Apply Termux-specific adaptations to a Claude Code standalone module graph.

Adaptation: search_shadow
  The official native binary embeds bfs/ugrep and its native prelude enables
  launchOptions.searchToolsOptIn(), which makes Claude Code inject `find` and
  `grep` shell functions that re-invoke the CLI binary as `bfs`/`ugrep`
  (multi-call). A bionic graft has no native prelude, so those functions call
  the normal CLI with `-G` and fail with "error: unknown option '-G'".

  Fix: locate the semantic getter for searchToolsOptIn() (minified function
  names change between Claude releases), patch it to return true, and force its
  module to compile from source (zero its bytecode pointer) so the patch takes
  effect.

Adaptation: native_updater
  installLatest() is how Claude's own auto-updater (TUI mount and every 30
  minutes), `claude update` and `claude install` install a release: it
  downloads the official glibc build into ~/.local/share/claude/versions.
  Fix: replace its body with a call to globalThis.__claudeTermuxInstallLatest,
  which runtime/20-self-update.js provides (the CI-approved release, built
  here). The auto-updater keeps its schedule and status line, so updates run
  in the background as on the desktop. The function is found by its
  "installLatest: joining in-flight call" log line.

Usage: adapt_graph.py <graph.bin> <out.bin> [--report report.json]

--report writes a machine-readable record of what was actually applied, so the
build manifest and release notes can name the adaptations from the run itself
instead of keeping a hardcoded list in sync by hand.
"""
import json, re, struct, sys

# Match behavior rather than a minified symbol such as KKn/$Xn. Those names are
# not API and changed in Claude Code 2.1.272 even though the getter did not.
SEARCH_OPT_IN_GETTER = re.compile(
    rb'function (?P<fn>[A-Za-z_$][A-Za-z0-9_$]*)\(\)\{return '
    rb'[A-Za-z_$][A-Za-z0-9_$]*\(\)\.host\.launchOptions\.'
    rb'searchToolsOptIn\(\)\}'
)

INSTALL_LATEST_ANCHOR = b'"installLatest: joining in-flight call"'
FUNCTION_HEAD = re.compile(rb'function (?P<fn>[A-Za-z_$][A-Za-z0-9_$]*)\((?P<params>[^()]*)\)\{')
INSTALL_LATEST_HOOK = b'return globalThis.__claudeTermuxInstallLatest(...arguments)'
MAX_FUNCTION_BYTES = 4096

def make_replacement(old: bytes, new_body: bytes) -> bytes:
    pad = len(old) - len(new_body) - 1
    return new_body + b' ' * pad + b'}'

def closing_brace(data, open_at: int):
    """Index of the `}` closing the `{` at open_at, skipping string and template
    literals (with ${...} nesting); None if not found within MAX_FUNCTION_BYTES."""
    stack = ['{']   # '{' code block, '`' template, '${' template expression
    i = open_at + 1
    end = min(len(data), open_at + MAX_FUNCTION_BYTES)
    while i < end:
        c = data[i:i + 1]
        if stack[-1] == '`':
            if c == b'\\':
                i += 2
                continue
            if c == b'`':
                stack.pop()
            elif data[i:i + 2] == b'${':
                stack.append('${')
                i += 1
        elif c in (b'"', b"'"):
            i += 1
            while i < end and data[i:i + 1] != c:
                i += 2 if data[i:i + 1] == b'\\' else 1
        elif c == b'`':
            stack.append('`')
        elif c == b'{':
            stack.append('{')
        elif c == b'}':
            stack.pop()
            if not stack:
                return i
        i += 1
    return None

def install_latest_function(data):
    """(start, end) of the installLatest() function: the closest function
    around the anchor whose braces enclose it."""
    sites = [m.start() for m in re.finditer(re.escape(INSTALL_LATEST_ANCHOR), data)]
    if len(sites) != 1:
        raise SystemExit(f'installLatest() log line found {len(sites)} times, expected 1; '
                         'graph layout changed?')
    anchor = sites[0]
    heads = list(FUNCTION_HEAD.finditer(data, max(0, anchor - MAX_FUNCTION_BYTES), anchor))
    for head in reversed(heads):
        close = closing_brace(data, head.end() - 1)
        if close is not None and close > anchor:
            return head, close + 1
    raise SystemExit('installLatest() not found around its log line; graph layout changed?')

def main():
    argv = sys.argv[1:]
    report_path = None
    if '--report' in argv:
        i = argv.index('--report')
        if i + 1 >= len(argv):
            raise SystemExit('--report needs a path')
        report_path = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 2:
        raise SystemExit('usage: adapt_graph.py <graph.bin> <out.bin> [--report report.json]')
    src, dst = argv
    data = bytearray(open(src, 'rb').read())
    n = len(data)
    o = n - 48
    byte_count, mod_off, mod_len, entry, argv0, argv1, flags = struct.unpack_from('<QIIIIII', data, o)
    stride = 52 if mod_len % 52 == 0 else 36
    if stride != 52:
        raise SystemExit(f'unsupported stride {stride}')
    count = mod_len // stride
    if byte_count != n - 48:
        raise SystemExit('byte_count mismatch')

    matches = list(SEARCH_OPT_IN_GETTER.finditer(data))
    if not matches:
        raise SystemExit('searchToolsOptIn() getter not found; graph layout changed?')
    patches = []
    for match in matches:
        old_fn = match.group(0)
        new_body = b'function ' + match.group('fn') + b'(){return!0'
        if len(new_body) > len(old_fn):
            raise SystemExit(f'replacement ({len(new_body)}B) is longer than getter '
                             f'({len(old_fn)}B); patching would shift the graph')
        new_fn = make_replacement(old_fn, new_body)
        data[match.start():match.end()] = new_fn
        patches.append((match.start(), new_fn))
    print(f'patched searchToolsOptIn() getter at {[site for site, _ in patches]}')

    head, end = install_latest_function(data)
    old_fn = bytes(data[head.start():end])
    new_body = b'function ' + head.group('fn') + b'(' + head.group('params') + b'){' + INSTALL_LATEST_HOOK
    if len(new_body) + 1 > len(old_fn):
        raise SystemExit(f'installLatest() ({len(old_fn)}B) is shorter than its replacement')
    new_fn = make_replacement(old_fn, new_body)
    data[head.start():end] = new_fn
    patches.append((head.start(), new_fn))
    print(f'patched installLatest() {head.group("fn").decode()} at {head.start()} '
          f'({len(old_fn)}B -> hook)')

    hits = [site for site, _ in patches]
    adaptations = ['search_shadow', 'native_updater']

    # Force every module whose contents contain a patched site to compile from source.
    forced = set()
    for h in hits:
        for i in range(count):
            rec = mod_off + i * stride
            co, cl = struct.unpack_from('<II', data, rec + 8)
            if co <= h < co + cl:
                forced.add(i)
                break
    if len(forced) < len(adaptations):
        # The bytes above are patched, but if no module record covers them the
        # runtime keeps executing that module's bytecode and never compiles the
        # patched source: the adaptation silently does nothing while the build
        # still reports success.
        raise SystemExit(f'graph patched at {hits} but no module record covers those '
                         'bytes, so nothing would be forced to compile from source and '
                         'the adaptation would be a silent no-op. Layout changed?')

    forced_names = []
    for i in sorted(forced):
        rec = mod_off + i * stride
        name_off, name_len = struct.unpack_from('<II', data, rec)
        name = data[name_off:name_off + name_len].decode('utf-8', 'replace')
        bc_off, bc_len = struct.unpack_from('<II', data, rec + 24)
        mi_off, mi_len = struct.unpack_from('<II', data, rec + 32)
        struct.pack_into('<II', data, rec + 24, 0, 0)
        struct.pack_into('<II', data, rec + 32, 0, 0)
        forced_names.append(name)
        print(f'  forced source compile: {name} (bytecode {bc_len}B -> 0, module_info {mi_len}B -> 0)')

    with open(dst, 'wb') as f:
        f.write(data)
    # Read the sites back rather than trusting the write: a patch that did not
    # land produces a build that looks fine and breaks grep/find again.
    with open(dst, 'rb') as f:
        for h, expected in patches:
            f.seek(h)
            if f.read(len(expected)) != expected:
                raise SystemExit(f'patched site {h} did not land in {dst}')
    print(f'wrote {dst} ({len(data)} bytes)')

    if report_path:
        with open(report_path, 'w') as f:
            json.dump({'adaptations': adaptations, 'sites': hits,
                       'forced_modules': forced_names}, f, indent=2)
            f.write('\n')
        print(f'wrote {report_path}')

if __name__ == '__main__':
    main()
