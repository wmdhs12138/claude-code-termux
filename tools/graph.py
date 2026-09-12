#!/usr/bin/env python3
"""Parse the module graph inside a Bun standalone .bun section.

New format (Bun >= 1.4): .bun section = [u64 BUN_COMPILED.size][graph][Offsets32][marker16]
Old format (Bun <= 1.3): graph at end of file with trailer + 8B tail.
"""
import struct, sys, json

MARKER = b"---- Bun! ----"

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
            out.append((nm(name), typ, off, sz))
        return out

def parse(path):
    sec = None
    for n, t, o, s in elf_sections(path):
        if n == '.bun':
            sec = (o, s)
    if not sec:
        raise SystemExit('no .bun')
    off, size = sec
    with open(path, 'rb') as f:
        f.seek(off)
        data = f.read(size)
    marker = data.rfind(MARKER)
    if marker < 0:
        raise SystemExit('no marker')
    if data[marker-1:marker] != b'\n':
        raise SystemExit('no newline before marker')
    o = marker - 1 - 32
    byte_count, mod_off, mod_len, entry, argv0, argv1, flags = struct.unpack_from('<QIIIIII', data, o)
    print(json.dumps({
        'section_off': off, 'section_size': size, 'first_u64': struct.unpack_from('<Q', data, 0)[0],
        'marker_at': marker, 'byte_count': byte_count, 'mod_off': mod_off, 'mod_len': mod_len,
        'entry': entry, 'argv0': argv0, 'argv1': argv1, 'flags': flags,
        'mod_area_mod36': mod_len % 36, 'mod_area_mod52': mod_len % 52,
        'stride': 52 if mod_len % 52 == 0 else (36 if mod_len % 36 == 0 else None),
        'count': mod_len // (52 if mod_len % 52 == 0 else 36) if mod_len else 0,
    }, indent=1))
    return data, byte_count, mod_off, mod_len, entry

def ptr(data, base, off):
    """StringPointer = u32 offset + u32 length, relative to base."""
    o, l = struct.unpack_from('<II', data, base + off)
    return o, l, data[o:o+l]

def dump_records(path, stride=52, limit=5):
    data, byte_count, mod_off, mod_len, entry = parse(path)
    # find graph base: try section-relative first
    n = mod_len // stride
    print(f"records: {n}")
    for i in range(min(n, limit)):
        rec = data[mod_off + i*stride: mod_off + (i+1)*stride]
        print(f"--- rec {i}: {rec.hex()}")
        for j in range(stride // 8):
            a, b = struct.unpack_from('<II', rec, j*8)
            print(f"   ptr{j}: off={a} len={b}", end='')
            if b < 200 and a + b <= len(data):
                s = data[a:a+b]
                print(f" data={s[:80]!r}")
            else:
                print()

if __name__ == '__main__':
    path = sys.argv[1]
    stride = int(sys.argv[2]) if len(sys.argv) > 2 else 52
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    dump_records(path, stride, limit)
