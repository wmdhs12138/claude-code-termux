#!/usr/bin/env python3
"""Extract the standalone module-graph payload from a .bun section.

Output file = section[8 : 8+payload_len] (the bytes the runtime sees after the
[u64 payload_len] prefix). Prints Offsets + flags summary.
"""
import struct, sys, json

TRAILER = b"\n---- Bun! ----\n"

def elf_sections(path):
    with open(path, 'rb') as f:
        d = f.read(64)
        shoff = struct.unpack('<Q', d[0x28:0x30])[0]
        shentsize = struct.unpack('<H', d[0x3a:0x3c])[0]
        shnum = struct.unpack('<H', d[0x3c:0x3e])[0]
        shstrndx = struct.unpack('<H', d[0x3e:0x40])[0]
        f.seek(shoff + shstrndx * shentsize)
        h = f.read(64)
        so = struct.unpack('<Q', h[0x18:0x20])[0]
        ss = struct.unpack('<Q', h[0x20:0x28])[0]
        f.seek(so)
        shstr = f.read(ss)
        def nm(n):
            e = shstr.find(b'\0', n)
            return shstr[n:e].decode()
        f.seek(shoff)
        out = []
        for i in range(shnum):
            h = f.read(shentsize)
            name, typ = struct.unpack('<II', h[:8])
            addr, off, sz = struct.unpack('<QQQ', h[0x10:0x28])
            out.append((nm(name), typ, off, sz, addr))
        return out

FLAG_NAMES = {
    0: 'DISABLE_DEFAULT_ENV_FILES', 1: 'DISABLE_AUTOLOAD_BUNFIG',
    2: 'DISABLE_AUTOLOAD_TSCONFIG', 3: 'DISABLE_AUTOLOAD_PACKAGE_JSON',
    4: 'SOURCE_TEXT_CONTIGUOUS', 5: 'HAS_SOURCE_HASHES',
    6: 'HAS_BUILTIN_BYTECODE', 7: 'HAS_BYTECODE_STRING_TABLE',
    8: 'HAS_STARTUP_MODULE_COUNT', 9: 'HAS_MODULE_INFO_STRING_TABLE',
    10: 'CROSS_COMPILED_BYTECODE',
}

def main():
    path, outpath = sys.argv[1], sys.argv[2]
    sec = None
    for n, t, o, s, a in elf_sections(path):
        if n == '.bun':
            sec = (o, s, a)
    if not sec:
        raise SystemExit('no .bun section')
    off, size, addr = sec
    with open(path, 'rb') as f:
        f.seek(off)
        data = f.read(size)
    payload_len = struct.unpack_from('<Q', data, 0)[0]
    payload = data[8:8+payload_len]
    section = {'off': off, 'size': size, 'addr': addr, 'payload_len': payload_len,
               'trailer_ok': payload.endswith(TRAILER)}
    o = payload_len - 32 - len(TRAILER)
    byte_count, mod_off, mod_len, entry, argv0, argv1, flags = struct.unpack_from('<QIIIIII', payload, o)
    # 52 = Bun >= 1.4 new-section records, 36 = the <= 1.3 layout (which this
    # toolchain does not support). Both divisibility facts go into the report:
    # if mod_len divides by both, the module count is an interpretation of the
    # numbers, not a measurement, and should not be quoted as a credential.
    if mod_len % 52 == 0:
        stride = 52
    elif mod_len % 36 == 0:
        raise SystemExit(f'module table is 36-strided ({mod_len} bytes), the pre-1.4 '
                         'layout this toolchain does not support')
    else:
        raise SystemExit(f'module table length {mod_len} is neither 52- nor 36-aligned')
    offsets = {'byte_count': byte_count, 'mod_off': mod_off, 'mod_len': mod_len,
               'mod_len_mod52': mod_len % 52, 'mod_len_mod36': mod_len % 36,
               'entry': entry, 'argv0': argv0, 'argv1': argv1, 'flags': hex(flags),
               'flags_set': [FLAG_NAMES.get(i, f'bit{i}') for i in range(32) if flags >> i & 1],
               'stride': stride, 'module_count': mod_len // stride}
    with open(outpath, 'wb') as f:
        f.write(payload)
    print(json.dumps({'section': section, 'offsets': offsets,
                      'output': {'path': outpath, 'bytes': len(payload)}}, indent=1))

if __name__ == '__main__':
    main()
