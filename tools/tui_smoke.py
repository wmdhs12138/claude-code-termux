#!/usr/bin/env python3
"""PTY smoke test for the native Claude TUI.

Launch the binary, require evidence that a TUI was rendered and verify that it
stays alive for the observation window. A process that exits/crashes early or
renders nothing fails the smoke test.

Usage: tui_smoke.py <binary> [seconds]
"""
import os
import pty
import re
import select
import signal
import sys
import time

MIN_TUI_BYTES = 256
MARKERS = (
    "Claude Code",
    "bypass permissions",
    "Quick safety check",
    "Accessing workspace",
)


def read_available(fd: int, out: bytearray, timeout: float) -> None:
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return
    try:
        chunk = os.read(fd, 65536)
    except OSError:
        return
    out.extend(chunk)


def kill_child_group(pid: int, sig: signal.Signals) -> None:
    """Signal the PTY child's process group, including helper subprocesses."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2

    binary = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) == 3 else 6.0
    if wait <= 0:
        print("seconds must be positive", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(binary, [binary], env)

    out = bytearray()
    status = None
    exited_early = False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        read_available(fd, out, min(0.3, max(0.0, deadline - time.monotonic())))
        done, child_status = os.waitpid(pid, os.WNOHANG)
        if done:
            status = child_status
            exited_early = True
            break

    forced_kill = False
    if status is None:
        for _ in range(2):
            try:
                os.write(fd, b"\x03")
            except OSError:
                break
            time.sleep(0.5)
            read_available(fd, out, 0)
            done, child_status = os.waitpid(pid, os.WNOHANG)
            if done:
                status = child_status
                break

    if status is None:
        forced_kill = True
        kill_child_group(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
    else:
        # The group leader may have exited while leaving a helper behind.
        kill_child_group(pid, signal.SIGKILL)

    try:
        os.close(fd)
    except OSError:
        pass

    text = out.decode("utf-8", "replace")
    # Full-screen TUIs freely insert cursor-control sequences between visible
    # words. Replace them with spaces before matching semantic screen text.
    visible = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|.)", " ", text)
    visible = " ".join(visible.split())
    markers = [marker for marker in MARKERS if marker in visible]
    has_ansi = "\x1b" in text
    # Require terminal controls, substantial screen output, and Claude-specific
    # semantic text. Any one signal alone is too easy for a crash log or stub.
    rendered = len(out) >= MIN_TUI_BYTES and has_ansi and bool(markers)

    print(
        f"bytes_captured={len(out)} exit_status={status} "
        f"exited_early={exited_early} forced_kill={forced_kill}"
    )
    print(f"has_ansi_escapes={has_ansi} min_tui_bytes={MIN_TUI_BYTES}")
    for marker in markers:
        print(f"marker_found: {marker}")
    print("--- first 600 chars ---")
    print(text[:600])

    if exited_early:
        print("smoke: FAIL: process exited before the observation window", file=sys.stderr)
        return 1
    if not rendered:
        print("smoke: FAIL: no recognizable TUI output", file=sys.stderr)
        return 1
    print("smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
