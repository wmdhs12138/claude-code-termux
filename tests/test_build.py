"""scripts/build.sh and the scripts it drives: fetch, Bun base cache, promotion, TUI smoke."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

from common import ROOT, SMOKE, FETCH, BUILD, ENSURE_BUN


class BuildScriptTests(unittest.TestCase):
    def test_shared_bun_cache_downloads_once_per_binary_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bun.zip"
            payload = b"immutable-bionic-bun"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("bun-linux-aarch64/bun", payload)
            archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            binary_sha = hashlib.sha256(payload).hexdigest()
            cache = root / "cache" / "bun-bases"
            args = [
                "bash", str(ENSURE_BUN), str(cache), archive.as_uri(),
                archive_sha, binary_sha,
            ]
            first = subprocess.run(args, text=True, capture_output=True)
            archive.unlink()
            second = subprocess.run(args, text=True, capture_output=True)
            target = cache / f"bun-{binary_sha}" / "bun"

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(target.read_bytes(), payload)
            self.assertIn("download verified", first.stderr)
            self.assertIn("(cached)", second.stderr)

    def test_shared_bun_cache_migrates_matching_legacy_base_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"legacy-verified-bionic-bun"
            binary_sha = hashlib.sha256(payload).hexdigest()
            seed = root / "claude-2.1.278-toolchain-" / "work" / "bun-android" / "bun"
            seed.parent.mkdir(parents=True)
            seed.write_bytes(payload)
            seed.chmod(0o755)
            cache = root / "bun-bases"
            proc = subprocess.run(
                [
                    "bash", str(ENSURE_BUN), str(cache), "https://invalid.invalid/bun.zip",
                    "a" * 64, binary_sha,
                ],
                text=True,
                capture_output=True,
            )
            target = cache / f"bun-{binary_sha}" / "bun"

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(target.read_bytes(), payload)
            self.assertIn("(migrated)", proc.stderr)

    def test_every_download_goes_through_one_helper(self):
        for path in (FETCH, BUILD, ENSURE_BUN):
            source = path.read_text()
            self.assertIn("scripts/download.sh", source.replace('"$HERE/download.sh"', "scripts/download.sh"))
            self.assertNotIn("--progress-bar", source)

    def run_download(self, tmp, *args, tty=False, progress_fd=False):
        """download.sh with a fake curl that records its arguments and prints a
        curl-style progress stream."""
        tmp = Path(tmp)
        fake = tmp / "bin"
        fake.mkdir(exist_ok=True)
        (fake / "curl").write_text(
            "#!/bin/sh\n"
            f'echo "$*" > "{tmp}/curl-args"\n'
            'while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
            'printf "payload" > "$out"\n'
            'case "$(cat "%s/curl-args")" in *--progress-bar*) '
            'printf "  10.0%%%%\\r  60.0%%%%\\r 100.0%%%%\\r" >&2 ;; esac\n' % tmp
        )
        (fake / "curl").chmod(0o755)
        env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}")
        cmd = ["bash", str(ROOT / "scripts" / "download.sh"), *args]
        if not tty:
            proc = subprocess.run(cmd, env=env, capture_output=True)
            return proc.returncode, (tmp / "curl-args").read_text(), proc.stderr
        import pty
        master, slave = pty.openpty()
        if progress_fd:
            # The caller logs stderr and hands the terminal over as fd 3.
            env["CLAUDE_CODE_TERMUX_PROGRESS_FD"] = "3"
            cmd = ["bash", "-c", 'exec 3<>"$1"; shift; exec "$@"', "_", os.ttyname(slave), *cmd]
            proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=slave)
        os.close(slave)
        shown = b""
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            shown += chunk
        os.close(master)
        return proc.returncode, (tmp / "curl-args").read_text(), shown

    def test_download_is_quiet_without_a_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, args, stderr = self.run_download(tmp, "--resume", "https://x/y", f"{tmp}/out", "  Label")
        self.assertEqual(rc, 0)
        self.assertIn("-fsSL", args)
        self.assertIn("-C -", args)
        self.assertEqual(stderr, b"")

    def test_download_draws_one_labelled_bar_on_a_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, args, shown = self.run_download(tmp, "https://x/y", f"{tmp}/out", "  Downloading x",
                                                tty=True)
        self.assertEqual(rc, 0)
        self.assertIn("--progress-bar", args)
        self.assertIn(b"\r  Downloading x [", shown)
        self.assertIn(b"100%", shown)

    def test_download_draws_on_the_progress_fd_when_the_caller_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, shown = self.run_download(tmp, "https://x/y", f"{tmp}/out", "  Downloading x",
                                             tty=True, progress_fd=True)
        self.assertEqual(rc, 0)
        self.assertIn(b"  Downloading x [", shown)

    def test_compact_progress_throttles_updates_and_preserves_errors(self):
        payload = b"0.1%\r0.9%\r4.9%\r5.0%\r5.4%\r10.0%\r100.0%\rcurl: (22) test error\n"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "compact-progress.py")],
            input=payload,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr.count(b"\r["), 4)
        self.assertIn(b"100%", proc.stderr)
        self.assertIn(b"curl: (22) test error", proc.stderr)

    def test_compact_progress_labels_the_bar(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "compact-progress.py"), "  Downloading x"],
            input=b"50.0%\r100.0%\r", capture_output=True,
        )
        self.assertIn(b"\r  Downloading x [##########..........]  50%", proc.stderr)

    def test_fetch_rejects_non_semver_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                ["bash", str(FETCH), "2.1.3/../../unexpected", tmp],
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid version", proc.stderr)

    def test_build_rejects_malformed_lock_before_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copy2(BUILD, root / "scripts" / "build.sh")
            (root / "versions.json").write_text("{}")
            proc = subprocess.run(
                ["bash", str(root / "scripts" / "build.sh"), "1.2.3"],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((root / "work" / ".claude-version").exists())

    def test_refresh_base_refuses_a_build_that_cannot_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copy2(BUILD, root / "scripts" / "build.sh")
            shutil.copy2(ROOT / "versions.json", root / "versions.json")
            env = dict(os.environ, REFRESH_BASE="1", SKIP_RUN="1")
            proc = subprocess.run(
                ["bash", str(root / "scripts" / "build.sh"), "1.2.3"],
                text=True, capture_output=True, env=env,
            )
            versions = (root / "versions.json").read_text()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("drop SKIP_RUN", proc.stderr)
        self.assertEqual(versions, (ROOT / "versions.json").read_text())

    def test_default_bun_dependency_is_immutable_and_fully_locked(self):
        versions = json.loads((ROOT / "versions.json").read_text())
        # An input file only: builds record what they did in dist/.
        self.assertEqual(set(versions), {"base_bun"})
        base = versions["base_bun"]
        self.assertNotIn("/download/canary/", base["url"])
        self.assertRegex(base["archive_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(base["binary_sha256"], r"^[0-9a-f]{64}$")

    def test_standalone_fetch_respects_build_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(FETCH, scripts / "fetch-claude.sh")
            (root / "work").mkdir()
            with open(root / "work" / ".build.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                proc = subprocess.run(
                    ["bash", str(scripts / "fetch-claude.sh"), "latest"],
                    text=True,
                    capture_output=True,
                )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("already running", proc.stderr)

    def test_promotion_drops_the_old_manifest_before_replacing_the_binary(self):
        script = BUILD.read_text()
        drop = script.index('rm -f "$DIST/build-manifest.json"')
        binary = script.index('mv -f "$CANDIDATE" "$DIST/claude"')
        manifest = script.index('mv -f "$MANIFEST_NEW" "$DIST/build-manifest.json"')
        self.assertLess(drop, binary)
        self.assertLess(binary, manifest)

    def test_build_writes_versions_json_only_for_an_accepted_refresh(self):
        script = BUILD.read_text()
        # Two uses: the pin read at the top and the write after promotion.
        self.assertEqual(script.count('"$ROOT/versions.json"'), 2)
        write = script.rindex('"$ROOT/versions.json"')
        guard = script.rindex('if [ "$REFRESH_BASE" = "1" ]; then', 0, write)
        self.assertNotIn("\nfi\n", script[guard:write])
        self.assertGreater(guard, script.index('mv -f "$MANIFEST_NEW" "$DIST/build-manifest.json"'))
        self.assertNotIn("evidence", script)


class BuildSmokeWiringTests(unittest.TestCase):
    """Wiring checks on scripts/build.sh.

    A behavioural test would have to stub every tool the build drives, and would
    then break on any unrelated change to build.sh. These assert only the
    properties that decide whether a candidate that renders nothing can reach
    dist/claude -- which is the failure 2.1.271 shipped.
    """

    @classmethod
    def setUpClass(cls):
        cls.script = BUILD.read_text()
        cls.lines = cls.script.splitlines()

    def line_of(self, needle):
        for i, line in enumerate(self.lines):
            if needle in line:
                return i
        self.fail(f"{needle!r} not found in build.sh")

    def smoke_section(self):
        start = self.line_of("# 5b. Render check.")
        return "\n".join(self.lines[start:self.line_of("tui-smoke.json")])

    def test_smoke_runs_on_the_candidate_not_the_installed_binary(self):
        line = self.lines[self.line_of("tui_smoke.py")]
        self.assertIn('"$CANDIDATE"', line)
        self.assertNotIn('"$DIST/claude"', line)

    def test_smoke_runs_before_the_candidate_is_promoted(self):
        self.assertLess(
            self.line_of("tui_smoke.py"),
            self.line_of('mv -f "$CANDIDATE" "$DIST/claude"'),
        )

    def test_smoke_runs_without_external_preload(self):
        section = self.smoke_section()
        self.assertNotIn("BUN_OPTIONS=", section)
        self.assertNotIn("--preload", section)
        # The launcher forces both; the check is only faithful if it does too.
        self.assertIn("USE_BUILTIN_RIPGREP=0", section)
        self.assertIn("DISABLE_AUTOUPDATER=1", section)

    def test_smoke_is_skipped_where_the_binary_cannot_execute(self):
        section = self.smoke_section()
        guard = 'if [ "${SKIP_RUN:-0}" = "1" ]; then'
        self.assertIn(guard, section)
        # Inside the else branch, not before the guard: on x64 CI the candidate
        # cannot run at all, and a bare invocation would fail the whole build.
        self.assertLess(section.index(guard), section.index("tui_smoke.py"))
        self.assertIn("TUI smoke skipped", section)

    def test_a_failed_render_check_keeps_the_working_binary(self):
        section = self.smoke_section()
        self.assertIn("die_kept", section)

    def test_manifest_records_whether_the_render_check_ran(self):
        self.assertIn('"tui_smoke": load(smoke_path)', self.script)


class GraftTests(unittest.TestCase):
    """tools/revive_patch.py on a minimal ELF shaped like a Bionic Bun base."""

    BUN_SLOT = 0x100
    SEG_END = 0x200     # file end of the writable PT_LOAD
    BSS_END = 0x8000
    SYMTAB = b"\xaa" * 64

    def base_elf(self):
        # One RW PT_LOAD [0, SEG_END) with .bss up to BSS_END. The base keeps
        # non-loaded data (.shstrtab, .symtab, section headers) right after
        # SEG_END, inside the .bss range, as the official Bun 1.4.3 does.
        names = b"\0.bun\0.shstrtab\0.symtab\0"
        shstrtab_off = self.SEG_END
        symtab_off = shstrtab_off + len(names)
        shoff = (symtab_off + len(self.SYMTAB) + 7) & ~7
        out = bytearray(shoff + 4 * 64)
        out[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
        struct.pack_into("<HHIQQQIHHHHHH", out, 16, 3, 183, 1, 0, 64, shoff,
                         0, 64, 56, 1, 64, 4, 2)
        struct.pack_into("<IIQQQQQQ", out, 64, 1, 6, 0, 0, 0,
                         self.SEG_END, self.BSS_END, 0x10000)
        out[0x110:0x11a] = b"Bun v1.4.3"
        out[shstrtab_off:shstrtab_off + len(names)] = names
        out[symtab_off:symtab_off + len(self.SYMTAB)] = self.SYMTAB
        for i, (name, kind, flags, addr, off, size) in enumerate([
            (0, 0, 0, 0, 0, 0),
            (1, 1, 3, self.BUN_SLOT, self.BUN_SLOT, 8),
            (6, 3, 0, 0, shstrtab_off, len(names)),
            (16, 2, 0, 0, symtab_off, len(self.SYMTAB)),
        ]):
            struct.pack_into("<IIQQQQIIQQ", out, shoff + i * 64,
                             name, kind, flags, addr, off, size, 0, 0, 8, 0)
        return bytes(out)

    def graft(self, base):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bun").write_bytes(base)
            (root / "graph.bin").write_bytes(
                b"/$bunfs/root/x.js" + b"\0" * 32 + b"\n---- Bun! ----\n")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "revive_patch.py"),
                 "--bun", str(root / "bun"), "--graph", str(root / "graph.bin"),
                 "--out", str(root / "out")],
                text=True, capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return (root / "out").read_bytes()

    def test_old_bss_extent_maps_as_zeros(self):
        out = self.graft(self.base_elf())
        payload = struct.unpack_from("<Q", out, self.BUN_SLOT)[0]
        self.assertGreaterEqual(payload, self.BSS_END)
        self.assertEqual(out[self.SEG_END:payload], bytes(payload - self.SEG_END))

    def test_section_headers_survive_outside_the_mapped_range(self):
        out = self.graft(self.base_elf())
        filesz = struct.unpack_from("<Q", out, 64 + 32)[0]
        shoff, = struct.unpack_from("<Q", out, 0x28)
        self.assertGreaterEqual(shoff, filesz)
        sections = {}
        for i in range(4):
            name, _, _, addr, off, size = struct.unpack_from(
                "<IIQQQQ", out, shoff + i * 64)
            sections[name] = (addr, off, size)
        names_off = sections[6][1]
        self.assertEqual(out[names_off:names_off + 5], b"\0.bun")
        self.assertEqual(sections[1][:2], (self.BUN_SLOT, self.BUN_SLOT))
        _, symtab_off, size = sections[16]
        self.assertGreaterEqual(symtab_off, filesz)
        self.assertEqual(out[symtab_off:symtab_off + size], self.SYMTAB)


class TuiSmokeTests(unittest.TestCase):
    def run_fixture(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture.py"
            fixture.write_text("#!/usr/bin/env python3\n" + body)
            fixture.chmod(0o755)
            return subprocess.run(
                [sys.executable, str(SMOKE), str(fixture), "0.25"],
                text=True,
                capture_output=True,
                timeout=5,
            )

    def test_accepts_live_claude_marker(self):
        proc = self.run_fixture(
            "import time\n"
            "print('\\x1b[2JClaude Code ' + ('screen ' * 60), flush=True)\n"
            "time.sleep(5)\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("smoke: PASS", proc.stdout)

    def test_rejects_ansi_without_claude_marker(self):
        proc = self.run_fixture(
            "import time\nprint('\\x1b[2Jnot the app', flush=True)\ntime.sleep(5)\n"
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no recognizable TUI output", proc.stderr)

    def test_rejects_generic_welcome_screen(self):
        proc = self.run_fixture(
            "import time\n"
            "print('\\x1b[2JWelcome ' + ('screen ' * 60), flush=True)\n"
            "time.sleep(5)\n"
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no recognizable TUI output", proc.stderr)

    def test_rejects_process_that_exits_early(self):
        proc = self.run_fixture("print('Claude Code', flush=True)\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("exited before", proc.stderr)


if __name__ == "__main__":
    unittest.main()
