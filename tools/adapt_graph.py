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

def make_replacement(old: bytes, new_body: bytes) -> bytes:
    pad = len(old) - len(new_body) - 1
    return new_body + b' ' * pad + b'}'

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
    hits = [site for site, _ in patches]
    adaptations = ['search_shadow']
    print(f'patched searchToolsOptIn() getter at {hits}')

    # Force every module whose contents contain a patched site to compile from source.
    forced = set()
    for h in hits:
        for i in range(count):
            rec = mod_off + i * stride
            co, cl = struct.unpack_from('<II', data, rec + 8)
            if co <= h < co + cl:
                forced.add(i)
                break
    if not forced:
        # The bytes above are patched, but if no module record covers them the
        # runtime keeps executing that module's bytecode and never compiles the
        # patched source: the adaptation silently does nothing while the build
        # still reports success.
        raise SystemExit(f'searchToolsOptIn() getter patched at {hits} but no module '
                         'record covers those bytes, so nothing would be forced to '
                         'compile from source and the adaptation would be a silent '
                         'no-op. Layout changed?')

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
