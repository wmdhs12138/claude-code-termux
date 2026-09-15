#!/usr/bin/env python3
"""Embed a JavaScript preload into a Bun standalone graph's entry module.

The resulting graph initializes the compatibility shim from inside the
standalone executable.  It therefore does not depend on BUN_OPTIONS or a file
next to the executable at runtime.

Usage: embed_preload.py <graph.bin> <preload.js> <out.bin> [--report FILE]
"""

import argparse
import json
import os
import struct


TRAILER = b"\n---- Bun! ----\n"
OFFSETS_SIZE = 32
TRAILER_SIZE = len(TRAILER)
STRIDE = 52


def fail(message):
    raise SystemExit(f"embed-preload: {message}")


def pointer(data, at):
    return struct.unpack_from("<II", data, at)


def shift_pointer(data, at, insertion, delta):
    offset, length = pointer(data, at)
    if length and offset >= insertion:
        struct.pack_into("<I", data, at, offset + delta)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("graph")
    parser.add_argument("preload")
    parser.add_argument("output")
    parser.add_argument("--report")
    args = parser.parse_args()

    original = bytearray(open(args.graph, "rb").read())
    preload = open(args.preload, "rb").read()
    if not preload.strip():
        fail("preload is empty")
    if original[-TRAILER_SIZE:] != TRAILER:
        fail("graph trailer is missing")

    offsets_at = len(original) - OFFSETS_SIZE - TRAILER_SIZE
    byte_count, modules_at, modules_len, entry, argv_at, argv_len, flags = (
        struct.unpack_from("<QIIIIII", original, offsets_at)
    )
    if byte_count != offsets_at:
        fail(f"byte_count {byte_count} != offsets position {offsets_at}")
    if not modules_len or modules_len % STRIDE:
        fail(f"unsupported module table length {modules_len}")
    module_count = modules_len // STRIDE
    if entry >= module_count:
        fail(f"entry point {entry} is outside {module_count} modules")

    entry_record = modules_at + entry * STRIDE
    source_at, source_len = pointer(original, entry_record + 8)
    if not source_len or source_at + source_len > offsets_at:
        fail("entry module has no valid source text")

    # End the IIFE defensively before the original entry source.  The leading
    # newline keeps a trailing // comment in a future preload from swallowing
    # the first line of Claude's entry module.
    injected = preload.rstrip() + b"\n;\n"
    delta = len(injected)
    output = original[:source_at] + injected + original[source_at:]

    new_modules_at = modules_at + (delta if modules_at >= source_at else 0)
    new_offsets_at = offsets_at + delta

    # Every StringPointer is graph-relative.  The entry source is the one
    # exception: it now begins at the insertion and spans preload + old source.
    for index in range(module_count):
        record = new_modules_at + index * STRIDE
        for field in (0, 8, 16, 24, 32, 40):
            if index == entry and field == 8:
                struct.pack_into("<II", output, record + field, source_at, source_len + delta)
            else:
                shift_pointer(output, record + field, source_at, delta)

    # Force the changed entry to compile from its new source.  Its serialized
    # bytecode, module-info and bytecode origin describe the unmodified source.
    for field in (24, 32, 40):
        struct.pack_into("<II", output, new_modules_at + entry * STRIDE + field, 0, 0)

    # Optional records follow the module table in flag order.  Update every
    # StringPointer they contain; scalar counts and file indices do not move.
    record_at = new_modules_at + modules_len
    has = lambda bit: bool(flags & (1 << bit))
    if has(5):  # source hashes
        # A zero hash means "not supplied".  The entry source changed and its
        # old hash must not be used for compile-cache or bytecode validation.
        struct.pack_into("<I", output, record_at + entry * 4, 0)
        record_at += module_count * 4
    if has(6):  # builtin bytecode: u32 count; {u32 id, StringPointer}[]
        builtin_count = struct.unpack_from("<I", output, record_at)[0]
        record_at += 4
        for _ in range(builtin_count):
            shift_pointer(output, record_at + 4, source_at, delta)
            record_at += 12
    if has(7):  # shared bytecode string table
        shift_pointer(output, record_at, source_at, delta)
        record_at += 8
    if has(8):  # startup module count
        record_at += 4
    if has(9):  # module-info string table
        shift_pointer(output, record_at, source_at, delta)
        record_at += 8
    if has(11):  # prelinked graph pointer, count, then file indices
        shift_pointer(output, record_at, source_at, delta)
        prelinked_count = struct.unpack_from("<I", output, record_at + 8)[0]
        record_at += 12 + prelinked_count * 4
    if has(12):  # runtime options flags + value
        record_at += 8
    if record_at > new_offsets_at:
        fail("optional graph records overlap the Offsets structure")

    if argv_len and argv_at >= source_at:
        argv_at += delta
    struct.pack_into(
        "<QIIIIII",
        output,
        new_offsets_at,
        new_offsets_at,
        new_modules_at,
        modules_len,
        entry,
        argv_at,
        argv_len,
        flags,
    )

    with open(args.output, "wb") as stream:
        stream.write(output)

    report = {
        "preload": os.path.basename(args.preload),
        "preload_bytes": delta,
        "entry_point": entry,
        "entry_source_offset": source_at,
        "entry_source_bytes_before": source_len,
        "entry_source_bytes_after": source_len + delta,
        "graph_bytes_before": len(original),
        "graph_bytes_after": len(output),
        "runtime_external_preload_required": False,
    }
    if args.report:
        with open(args.report, "w") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
