"""scripts/build.sh and the scripts it drives: fetch, Bun base cache, promotion, TUI smoke."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
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
                archive_sha, binary_sha, str(ROOT / "scripts" / "compact-progress.py"),
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
                    "a" * 64, binary_sha, str(ROOT / "scripts" / "compact-progress.py"),
                ],
                text=True,
                capture_output=True,
            )
            target = cache / f"bun-{binary_sha}" / "bun"

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(target.read_bytes(), payload)
            self.assertIn("(migrated)", proc.stderr)

    def test_large_downloads_use_single_line_bar_only_in_terminals(self):
        for source in (FETCH.read_text(), BUILD.read_text()):
            self.assertIn('if [ -t 2 ]; then', source)
            self.assertIn('--progress-bar', source)
            self.assertIn('scripts/compact-progress.py', source)
            self.assertIn('curl -fsSL', source)
            self.assertIn('--show-error', source)

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
