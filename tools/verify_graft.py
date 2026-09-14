#!/usr/bin/env python3
"""Verify the graft closure of a built ELF without executing it.

Walks exactly what the runtime walks at startup:

  .bun section header (sh_addr == sh_offset, identity mapped)
      -> u64 size field == payload vaddr   (plain-offset mode, bun >= 1.4)
      -> u64 length prefix at that vaddr
      -> [len bytes of module graph][Offsets struct][16B trailer]

A broken link here means the runtime reads garbage as a length or as the
module table -- historically a segfault at address 0x40. On x64 CI the
artifact cannot be executed at all, so this is the only structural gate that
can tell a real graft from a base Bun with the blob appended (or from a build
where the size field was never published).

Usage: verify_graft.py <elf> [expected-payload-size]
Prints one JSON object of verified facts on stdout; exits 1 with a reason on
stderr. The optional size is the adapted graph that was fed in -- passing it
ties the artifact to its input instead of just checking self-consistency.
"""
import json
import struct
import sys

TRAILER = b"\n---- Bun! ----\n"
OFFSETS_SIZE = 32      # StandaloneModuleGraph.Offsets extern struct
STRIDE = 52            # module record size in the new-section format
TRAILER_SIZE = len(TRAILER)


def fail(msg):
    print(f"verify-graft: FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def section_by_name(d, want):
    if d[:4] != b"\x7fELF":
        fail("not an ELF")
    if d[4] != 2 or d[5] != 1:
        fail("not a little-endian ELF64")
    if struct.unpack_from("<H", d, 18)[0] != 0xB7:
        fail("not aarch64")
    shoff = struct.unpack_from("<Q", d, 0x28)[0]
    shentsize = struct.unpack_from("<H", d, 0x3A)[0]
    shnum = struct.unpack_from("<H", d, 0x3C)[0]
    shstrndx = struct.unpack_from("<H", d, 0x3E)[0]
    if not shoff or not shnum or shstrndx >= shnum:
        fail("no usable section header table")

    def raw(i):
        o = shoff + i * shentsize
        (nameoff,) = struct.unpack_from("<I", d, o)
        addr, off, size = struct.unpack_from("<QQQ", d, o + 16)
        return nameoff, addr, off, size

    _, _, so, ss = raw(shstrndx)
    shstr = d[so:so + ss]
    for i in range(shnum):
        nameoff, addr, off, size = raw(i)
        end = shstr.find(b"\0", nameoff)
        if shstr[nameoff:end] == want:
            return addr, off, size
    fail(f"no {want.decode()} section")


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: verify_graft.py <elf> [expected-payload-size]")
    path = sys.argv[1]
    expected = int(sys.argv[2]) if len(sys.argv) > 2 else None
    with open(path, "rb") as f:
        d = f.read()

    addr, off, _size = section_by_name(d, b".bun")
    if addr != off:
        fail(f".bun addr {addr:#x} != offset {off:#x}; identity mapping expected")

    # The size field lives inside .bun and holds the payload vaddr as a plain
    # constant (plain-offset mode). Zero means revive_patch never ran on this
    # file -- the runtime would see no graph at all.
    if addr + 8 > len(d):
        fail(".bun size field is outside the file")
    payload_vaddr = struct.unpack_from("<Q", d, addr)[0]
    if payload_vaddr == 0:
        # Two ways to get here, and they need different fixes: .bun was never
        # written, or this is reloc-mode output -- the pre-1.4 scheme, which
        # revive_patch falls back to when the base ELF carries no Bun version
        # string, and which SIGSEGVs pre-init on a >=1.4 base.
        fail("size field is 0: no payload vaddr published. Either .bun was never "
             "written, or this is reloc-mode output (the pre-1.4 relocation scheme "
             "revive_patch falls back to when the base has no Bun version string), "
             "which this toolchain does not support")
    if payload_vaddr + 8 > len(d):
        fail(f"payload vaddr {payload_vaddr:#x} is outside the file ({len(d)} bytes)")

    n = struct.unpack_from("<Q", d, payload_vaddr)[0]
    if n < OFFSETS_SIZE + TRAILER_SIZE:
        fail(f"length prefix {n} is too small to be a module graph")
    if payload_vaddr + 8 + n > len(d):
        fail(f"payload [{payload_vaddr:#x}, {payload_vaddr + 8 + n:#x}) runs past EOF ({len(d)})")
    payload = d[payload_vaddr + 8:payload_vaddr + 8 + n]
    if not payload.endswith(TRAILER):
        fail(f"payload does not end with the trailer; tail={payload[-16:]!r}")

    o = n - OFFSETS_SIZE - TRAILER_SIZE
    byte_count, mod_off, mod_len, entry, _a0, _a1, flags = struct.unpack_from(
        "<QIIIIII", payload, o)
    if byte_count != o:
        fail(f"byte_count {byte_count} != payload size before Offsets ({o})")
    if mod_len == 0 or mod_len % STRIDE:
        fail(f"module table length {mod_len} is not a positive multiple of {STRIDE}")
    count = mod_len // STRIDE
    if mod_off + mod_len > o:
        fail(f"module table [{mod_off:#x}, {mod_off + mod_len:#x}) overlaps the Offsets struct at {o:#x}")
    if entry >= count:
        fail(f"entry point {entry} is outside the module table ({count} modules)")
    if expected is not None and n != expected:
        fail(f"embedded payload is {n} bytes but the adapted graph is {expected}")

    print(json.dumps({
        "payload_vaddr": payload_vaddr,
        "payload_size": n,
        "modules": count,
        "entry_point": entry,
        "flags": f"0x{flags:x}",
        "trailer_ok": True,
    }, indent=1))


if __name__ == "__main__":
    main()
