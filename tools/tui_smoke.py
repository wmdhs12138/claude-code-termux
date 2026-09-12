#!/usr/bin/env python3
"""PTY smoke test: launch the native claude TUI, capture what it renders,
send Ctrl+C twice, and report whether it produced terminal output without
crashing. Usage: tui_smoke.py <binary> [seconds]"""
import os, pty, select, signal, sys, time

def main():
    binary = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    env = dict(os.environ)
    env.setdefault('TERM', 'xterm-256color')
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(binary, [binary], env)
    out = bytearray()
    deadline = time.time() + wait
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out.extend(chunk)
    os.write(fd, b'\x03')
    time.sleep(0.5)
    os.write(fd, b'\x03')
    time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _, status = os.waitpid(pid, 0)
    text = out.decode('utf-8', 'replace')
    print(f"bytes_captured={len(out)} exit_status={status}")
    print(f"has_ansi_escapes={chr(27) in text}")
    for kw in ('Claude Code', 'Welcome', 'bypass permissions', '? for shortcuts', 'DeepSeek'):
        if kw in text:
            print(f"marker_found: {kw}")
    print("--- first 600 chars ---")
    print(text[:600])

if __name__ == '__main__':
    main()
