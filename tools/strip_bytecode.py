#!/usr/bin/env python3
"""Strip embedded bytecode from a Bun standalone module-graph payload.

Zeroes every module record's `bytecode` and `module_info` StringPointers, plus
all entries of the builtin-bytecode table, so the runtime compiles everything
from source instead of executing version-locked bytecode.

Record layout (Bun >= 1.4, stride 52):
  name, contents, sourcemap, bytecode, module_info, bytecode_origin_path
  + 4 bytes (encoding, loader, module_format, side)
"""
import struct, sys, json

def main():
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, 'rb') as f:
        data = bytearray(f.read())
    n = len(data)
    o = n - 48
    byte_count, mod_off, mod_len, entry, argv0, argv1, flags = struct.unpack_from('<QIIIIII', data, o)
    stride = 52 if mod_len % 52 == 0 else 36
    count = mod_len // stride
    if stride != 52:
        raise SystemExit(f'unsupported stride {stride}')
    print(json.dumps({'len': n, 'byte_count': byte_count, 'mod_off': mod_off,
                      'mod_len': mod_len, 'entry': entry, 'flags': hex(flags),
                      'modules': count}, indent=1))
    if byte_count != n - 48:
        raise SystemExit(f'byte_count {byte_count} != len-48 {n-48}')
    stripped_bc = stripped_mi = 0
    for i in range(count):
        rec = mod_off + i * stride
        for field in (24, 32, 40):  # bytecode, module_info, bytecode_origin_path
            off, ln = struct.unpack_from('<II', data, rec + field)
            if ln:
                struct.pack_into('<II', data, rec + field, 0, 0)
                if field == 24: stripped_bc += 1
                if field == 32: stripped_mi += 1
    # trailing area after module table
    p = mod_off + mod_len
    has = lambda bit: bool(flags >> bit & 1)
    if has(5):  # HAS_SOURCE_HASHES
        p += 4 * count
    builtin_zeroed = 0
    if has(6):  # HAS_BUILTIN_BYTECODE
        bcount = struct.unpack_from('<I', data, p)[0]
        p += 4
        for i in range(bcount):
            _id, boff, blen = struct.unpack_from('<III', data, p)
            if blen:
                struct.pack_into('<II', data, p + 4, 0, 0)
                builtin_zeroed += 1
            p += 12
        print(f'builtin bytecode entries: {bcount}, zeroed: {builtin_zeroed}')
    print(json.dumps({'stripped_module_bytecode': stripped_bc,
                      'stripped_module_info': stripped_mi,
                      'zeroed_builtin_bytecode': builtin_zeroed}, indent=1))
    with open(dst, 'wb') as f:
        f.write(data)
    print(f'wrote {dst} ({len(data)} bytes)')

if __name__ == '__main__':
    main()
