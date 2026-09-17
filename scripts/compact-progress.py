#!/usr/bin/env python3
"""Reduce curl's chatty progress stream to one bar update per 5 percent."""

import os
import re
import sys

last_step = -1
line = bytearray()


def show(fragment: bytes) -> None:
    global last_step
    value = fragment.decode("utf-8", "replace").strip()
    match = re.search(r"(?<!\d)(\d{1,3})(?:\.\d+)?%", value)
    if match:
        percent = min(100, int(match.group(1)))
        step = percent // 5
        if step > last_step:
            last_step = step
            filled = step
            sys.stderr.write(f"\r[{'#' * filled}{'.' * (20 - filled)}] {step * 5:3d}%")
            sys.stderr.flush()
        return
    if value.startswith(("curl:", "Warning:")):
        if last_step >= 0:
            sys.stderr.write("\n")
            last_step = -1
        sys.stderr.write(value + "\n")
        sys.stderr.flush()


while chunk := os.read(sys.stdin.fileno(), 4096):
    for byte in chunk:
        if byte in (10, 13):
            if line:
                show(bytes(line))
                line.clear()
        else:
            line.append(byte)
if line:
    show(bytes(line))
if last_step >= 0:
    sys.stderr.write("\n")
