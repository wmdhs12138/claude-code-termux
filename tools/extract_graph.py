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
    print(json.dumps({'section_off': off, 'section_size': size, 'section_addr': addr,
                      'payload_len': payload_len, 'trailer_ok': payload.endswith(TRAILER)}, indent=1))
    o = payload_len - 32 - len(TRAILER)
    byte_count, mod_off, mod_len, entry, argv0, argv1, flags = struct.unpack_from('<QIIIIII', payload, o)
    stride = 52 if mod_len % 52 == 0 else 36
    print(json.dumps({'byte_count': byte_count, 'mod_off': mod_off, 'mod_len': mod_len,
                      'entry': entry, 'argv0': argv0, 'argv1': argv1, 'flags': hex(flags),
                      'flags_set': [FLAG_NAMES.get(i, f'bit{i}') for i in range(32) if flags >> i & 1],
                      'stride': stride, 'module_count': mod_len // stride}, indent=1))
    with open(outpath, 'wb') as f:
        f.write(payload)
    print(f"wrote {outpath} ({len(payload)} bytes)")

if __name__ == '__main__':
    main()
