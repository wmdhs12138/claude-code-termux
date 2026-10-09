#!/usr/bin/env python3
"""Reduce curl's chatty progress stream to one bar update per 5 percent.

  curl --progress-bar ... 2>&1 | compact-progress.py [LABEL]

Writes `LABEL [####......]  45%` to stderr, redrawn in place, and narrows the
bar so the line never wraps on a small phone terminal (a wrapped line cannot be
redrawn and turns into a column of bars). curl's own error lines pass through.
"""

import os
import re
import sys

label = sys.argv[1] if len(sys.argv) > 1 else ""
last_step = -1
line = bytearray()


def layout() -> tuple[str, int]:
    """The label and bar width that fit the terminal on one line."""
    try:
        columns = os.get_terminal_size(sys.stderr.fileno()).columns
    except OSError:
        columns = 0
    if columns <= 0:  # not a terminal, or one that reports no size
        columns = 80
    # " [" + bar + "] " + "100%" + a spare column, so the line never wraps
    width = max(5, min(20, columns - len(label) - 9))
    text = label
    overflow = len(text) + width + 9 - columns
    if overflow > 0:
        text = text[:max(0, len(text) - overflow - 1)] + "…"
    return text, width


def show(fragment: bytes) -> None:
    global last_step
    value = fragment.decode("utf-8", "replace").strip()
    match = re.search(r"(?<!\d)(\d{1,3})(?:\.\d+)?%", value)
    if match:
        percent = min(100, int(match.group(1)))
        step = percent // 5
        if step > last_step:
            last_step = step
            text, width = layout()
            filled = step * width // 20
            prefix = f"{text} " if text else ""
            sys.stderr.write(f"\r{prefix}[{'#' * filled}{'.' * (width - filled)}] {step * 5:3d}%")
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
