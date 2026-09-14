#!/usr/bin/env python3
"""Apply Termux-specific adaptations to a Claude Code standalone module graph.

Adaptation: search_shadow
  The official native binary embeds bfs/ugrep and its native prelude enables
  launchOptions.searchToolsOptIn(), which makes Claude Code inject `find` and
  `grep` shell functions that re-invoke the CLI binary as `bfs`/`ugrep`
  (multi-call). A bionic graft has no native prelude, so those functions call
  the normal CLI with `-G` and fail with "error: unknown option '-G'".

  Fix: patch KKn() (the only caller is the Qb() gate) to return true, which
  disables the shadowing so system find/grep are used, and force its module to
  compile from source (zero its bytecode pointer) so the patch takes effect.

Usage: adapt_graph.py <graph.bin> <out.bin> [--report report.json]

--report writes a machine-readable record of what was actually applied, so the
build manifest and release notes can name the adaptations from the run itself
instead of keeping a hardcoded list in sync by hand.
"""
import json, struct, sys

OLD_KKN = b'function KKn(){return n().host.launchOptions.searchToolsOptIn()}'
NEW_BODY = b'function KKn(){return!0'
if len(NEW_BODY) > len(OLD_KKN):
    # The patch is length-preserving on purpose. A longer body would push every
    # following byte along, quietly invalidating offsets across the whole graph.
    # Not an assert: `python3 -O` drops those, and this one guards corruption.
    raise SystemExit(f'new body ({len(NEW_BODY)}B) is longer than the original '
                     f'({len(OLD_KKN)}B); patching in place would shift the graph')

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

    new_fn = make_replacement(OLD_KKN, NEW_BODY)
    hits = []
    start = 0
    while True:
        i = data.find(OLD_KKN, start)
        if i < 0:
            break
        hits.append(i)
        data[i:i + len(OLD_KKN)] = new_fn
        start = i + len(OLD_KKN)
    if not hits:
        raise SystemExit('KKn() signature not found; graph layout changed?')
    adaptations = ['search_shadow']
    print(f'patched KKn() at {hits} -> {new_fn[:40]!r}...')

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
        raise SystemExit(f'KKn() patched at {hits} but no module record covers those '
                         'bytes, so nothing would be forced to compile from source '
                         'and the adaptation would be a silent no-op. Layout changed?')

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
        for h in hits:
            f.seek(h)
            if f.read(len(new_fn)) != new_fn:
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
