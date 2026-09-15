#!/usr/bin/env python3
"""Fail the build when Claude Code's native Ink ABI drifts away from the
surface tools/cellsegmenter-polyfill.js implements.

Since 2.1.271 src/ink renders through Bun.ant.CellSegmenter, an interface of
Anthropic's private @anthropic-ai/bun-internal runtime. The polyfill is written
against the member surface the module graph actually uses. If upstream adds,
removes or renames a member, the grafted Android binary throws on its first
render and shows a blank terminal -- usually discovered by a user, not by CI.
Scanning the extracted graph makes that a build failure instead.

Checks:
  * Bun.ant.* reads stay inside the known set. getPeerPid/getPeerUid/
    memoryPressureLevel/setDumpable are optional probes that degrade by
    themselves on runtimes that lack them.
  * Modules that construct Bun.ant.CellSegmenter only call the native members
    the polyfill implements, and still call every member it relies on.
  * setCell() is reported but not required: it is part of the ABI and the
    polyfill implements it, yet current src/ink only uses it for the ellipsis
    path.

Usage: check_native_abi.py <graph.bin> [--report report.json]
"""
import json
import re
import struct
import sys

KNOWN_BUN_ANT = {
    'CellSegmenter',
    'getPeerPid',
    'getPeerUid',
    'memoryPressureLevel',
    'setDumpable',
}

REQUIRED_NATIVE = {'segment', 'paint', 'graphemes', 'sgrKeys', 'sgrCloseKeys', 'uris'}
KNOWN_NATIVE = REQUIRED_NATIVE | {'setCell'}

BUN_ANT_MEMBER = re.compile(rb'Bun\.ant\??\.([A-Za-z_$][\w$]*)')
CELL_SEGMENTER = re.compile(rb'Bun\.ant\??\.CellSegmenter')
NATIVE_MEMBER = re.compile(rb'\.native\.([A-Za-z_$][\w$]*)')
SET_CELL = re.compile(rb'\.setCell\s*\(')


class DriftError(Exception):
    pass


def module_records(data):
    n = len(data)
    byte_count, mod_off, mod_len, *_ = struct.unpack_from('<QIIIIII', data, n - 48)
    stride = 52 if mod_len % 52 == 0 else 36
    if stride != 52:
        raise DriftError(f'unsupported module stride {stride}')
    if byte_count != n - 48:
        raise DriftError('byte_count does not match the payload length')
    return mod_off, mod_len, stride, mod_len // stride


def analyze(data):
    mod_off, mod_len, stride, count = module_records(data)
    bun_ant = set()
    cell_modules = []
    native = set()
    set_cell = False
    for i in range(count):
        rec = mod_off + i * stride
        name_off, name_len, src_off, src_len = struct.unpack_from('<IIII', data, rec)
        name = data[name_off:name_off + name_len].decode('utf-8', 'replace')
        src = data[src_off:src_off + src_len]
        members = {m.group(1).decode() for m in BUN_ANT_MEMBER.finditer(src)}
        if not members:
            continue
        bun_ant |= members
        if CELL_SEGMENTER.search(src):
            cell_modules.append(name)
            native |= {m.group(1).decode() for m in NATIVE_MEMBER.finditer(src)}
            set_cell = set_cell or SET_CELL.search(src) is not None

    report = {
        'required': bool(cell_modules),
        'modules': cell_modules,
        'bun_ant_members': sorted(bun_ant),
        'native_members': sorted(native),
        'set_cell': set_cell,
        'polyfill': 'tools/cellsegmenter-polyfill.js',
    }

    unknown_ant = bun_ant - KNOWN_BUN_ANT
    if unknown_ant:
        raise DriftError(
            'new Bun.ant interface(s) ' + ', '.join(sorted(unknown_ant)) +
            ' appeared; the CellSegmenter polyfill does not implement them')
    if not cell_modules:
        return report

    missing = REQUIRED_NATIVE - native
    unknown_native = native - KNOWN_NATIVE
    if missing:
        raise DriftError(
            'CellSegmenter no longer uses ' + ', '.join(sorted(missing)) +
            '; the polyfill is written against the old ABI')
    if unknown_native:
        raise DriftError(
            'CellSegmenter uses new native member(s) ' + ', '.join(sorted(unknown_native)) +
            '; extend tools/cellsegmenter-polyfill.js before shipping')
    return report


def main():
    argv = sys.argv[1:]
    report_path = None
    if '--report' in argv:
        i = argv.index('--report')
        if i + 1 >= len(argv):
            raise SystemExit('--report needs a path')
        report_path = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 1:
        raise SystemExit('usage: check_native_abi.py <graph.bin> [--report report.json]')

    with open(argv[0], 'rb') as f:
        data = f.read()
    try:
        report = analyze(data)
    except DriftError as exc:
        print(f'native-abi: DRIFT: {exc}', file=sys.stderr)
        raise SystemExit(1)

    if report['required']:
        print('native-abi: CellSegmenter ok (' +
              ', '.join(report['native_members']) +
              (' + setCell' if report['set_cell'] else '') + ')')
    else:
        print('native-abi: no Bun.ant.CellSegmenter in this graph (pre-2.1.271)')

    if report_path:
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
            f.write('\n')


if __name__ == '__main__':
    main()
