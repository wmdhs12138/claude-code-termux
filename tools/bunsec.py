#!/usr/bin/env python3
"""Inspect the .bun section of a Bun standalone ELF."""
import struct, sys, os, re

def elf_sections(path):
    with open(path, 'rb') as f:
        d = f.read(64)
        if d[:4] != b'\x7fELF':
            raise SystemExit('not an ELF')
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
            return shstr[n:e].decode('utf-8', 'replace')
        f.seek(shoff)
        secs = []
        for i in range(shnum):
            h = f.read(shentsize)
            name, typ = struct.unpack('<II', h[:8])
            addr, off, sz = struct.unpack('<QQQ', h[0x10:0x28])
            secs.append({'name': nm(name), 'type': typ, 'addr': addr, 'off': off, 'size': sz})
        return secs

def get_bun(path):
    for s in elf_sections(path):
        if s['name'] == '.bun':
            return s
    raise SystemExit('no .bun section')

def main():
    path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'summary'
    sec = get_bun(path)
    with open(path, 'rb') as f:
        f.seek(sec['off'])
        data = f.read(sec['size'])
    print(f"file={path} .bun off=0x{sec['off']:x} size={sec['size']} (0x{sec['size']:x})")
    if len(data) >= 8:
        print(f"first u64 = 0x{struct.unpack('<Q', data[:8])[0]:x}  (size-8 = 0x{sec['size']-8:x})")
    idx = data.rfind(b'---- Bun! ----')
    print(f"marker at section offset {idx} (from end: {len(data)-idx})")
    if mode == 'dump':
        print(data.hex())
        print(''.join(chr(c) if 32 <= c < 127 else '.' for c in data))
        return
    if mode == 'small' and len(data) <= 8192:
        print(''.join(chr(c) if 32 <= c < 127 else '.' for c in data))
        return
    # summary: histogram of printable ratio per 64KB and printable runs
    import collections
    step = 65536
    print('offset(MB) ascii%')
    for off in range(0, len(data), step):
        chunk = data[off:off+step]
        pr = sum(1 for c in chunk if 32 <= c < 127 or c in (9, 10, 13)) / len(chunk)
        if pr > 0.05:
            print(f"{off/1048576:8.1f} {pr:5.1%}")
    runs = [(m.start(), m.end()-m.start()) for m in re.finditer(rb'[ -~]{200,}', data)]
    print(f"printable runs >=200 chars: {len(runs)}, total chars: {sum(l for _,l in runs)}")
    for off, ln in runs[:5]:
        print(f"  at {off} len {ln}: {data[off:off+80].decode('ascii','replace')}")

if __name__ == '__main__':
    main()
