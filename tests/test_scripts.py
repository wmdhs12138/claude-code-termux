import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "tui_smoke.py"
FETCH = ROOT / "scripts" / "fetch-claude.sh"
BUILD = ROOT / "scripts" / "build.sh"
UPDATE = ROOT / "scripts" / "update.sh"


class VersionValidationTests(unittest.TestCase):
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

    def test_failed_base_refresh_preserves_known_good_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            bun = root / "work" / "bun-android" / "bun"
            scripts.mkdir(parents=True)
            bun.parent.mkdir(parents=True)
            shutil.copy2(BUILD, scripts / "build.sh")
            bun.write_bytes(b"known-good-bun")
            bun.chmod(0o755)
            bun_sha = hashlib.sha256(bun.read_bytes()).hexdigest()
            (root / "versions.json").write_text(
                json.dumps(
                    {
                        "claude": "1.2.3",
                        "claude_linux_arm64_sha256": "0" * 64,
                        "base_bun": {
                            "archive_sha256": "2" * 64,
                            "binary_sha256": bun_sha,
                        },
                    }
                )
            )
            env = dict(os.environ, REFRESH_BASE="1")
            proc = subprocess.run(
                ["bash", str(scripts / "build.sh"), "1.2.3"],
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(bun.read_bytes(), b"known-good-bun")

    def test_default_bun_dependency_is_immutable_and_fully_locked(self):
        versions = json.loads((ROOT / "versions.json").read_text())
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
            with open(root / ".build.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                proc = subprocess.run(
                    ["bash", str(scripts / "fetch-claude.sh"), "latest"],
                    text=True,
                    capture_output=True,
                )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("already running", proc.stderr)

    def test_interrupted_promotion_recovery_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            dist = root / "dist"
            evidence = root / "evidence"
            scripts.mkdir()
            dist.mkdir()
            evidence.mkdir()
            shutil.copy2(BUILD, scripts / "build.sh")
            old_versions = json.dumps(
                {
                    "claude": "1.2.3",
                    "claude_linux_arm64_sha256": "0" * 64,
                    "base_bun": {
                        "archive_sha256": "2" * 64,
                        "binary_sha256": "1" * 64,
                    },
                }
            )
            files = (
                (dist / "claude", dist / ".claude.before-promote", "old-bin"),
                (
                    dist / "build-manifest.json",
                    dist / ".build-manifest.before-promote",
                    "old-manifest",
                ),
                (root / "versions.json", root / ".versions.json.before-promote", old_versions),
                (
                    evidence / "build-manifest.json",
                    evidence / ".build-manifest.before-promote",
                    "old-evidence",
                ),
            )
            for target, backup, old in files:
                target.write_text("partly-new")
                backup.write_text(old)
            # Model a second interruption after one target was already restored:
            # backups remain intact, so replaying recovery is safe.
            files[0][0].write_text("old-bin")
            (root / ".promotion-in-progress").write_text("1 1 1 1\n")
            proc = subprocess.run(
                ["bash", str(scripts / "build.sh"), "1.2.3"],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            for target, _, old in files:
                self.assertEqual(target.read_text(), old)
            self.assertFalse((root / ".promotion-in-progress").exists())


class UpdateRecoveryTests(unittest.TestCase):
    def test_broken_binary_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            scripts = root / "scripts"
            fake_bin_dir = Path(tmp) / "fake-bin"
            home = Path(tmp) / "home"
            scripts.mkdir(parents=True)
            fake_bin_dir.mkdir()
            home.mkdir()
            shutil.copy2(UPDATE, scripts / "update.sh")

            current = root / "claude"
            # A child inheriting stdout used to keep the updater's command
            # substitution open forever after the main process exited.
            current.write_text("#!/bin/sh\n(sleep 30) &\nexit 1\n")
            current.chmod(0o755)
            (scripts / "launcher.sh").write_text("#!/bin/sh\n# @ROOT@\n")
            build = scripts / "build.sh"
            build.write_text(
                "#!/bin/sh\n"
                "cat > \"$CLAUDE_CODE_TERMUX_BIN\" <<'EOF'\n"
                "#!/bin/sh\necho '1.2.3 (Claude Code)'\nEOF\n"
                "chmod +x \"$CLAUDE_CODE_TERMUX_BIN\"\n"
            )
            build.chmod(0o755)
            curl = fake_bin_dir / "curl"
            curl.write_text("#!/bin/sh\necho 1.2.3\n")
            curl.chmod(0o755)

            env = dict(os.environ)
            env.update(
                {
                    "PATH": f"{fake_bin_dir}:{env['PATH']}",
                    "HOME": str(home),
                    "CLAUDE_CODE_TERMUX_ROOT": str(root),
                    "CLAUDE_CODE_TERMUX_BIN": str(current),
                    "CLAUDE_CODE_TERMUX_PROBE_TIMEOUT": "0.25",
                }
            )
            proc = subprocess.run(
                ["bash", str(scripts / "update.sh"), "--force"],
                text=True,
                capture_output=True,
                env=env,
            )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("current binary is not runnable", proc.stderr)
        self.assertIn("updated: 1.2.3 (Claude Code)", proc.stdout)


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


class ReleaseNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / ".github" / "release_notes.py"
        spec = importlib.util.spec_from_file_location("release_notes", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_uses_manifest_source_hash_and_pinned_repro_command(self):
        manifest = {
            "claude": "9.8.7",
            "claude_linux_arm64_sha256": "new-source-hash",
            "output_sha256": "output-hash",
            "base_bun": {},
        }
        versions = {
            "claude": "9.8.6",
            "claude_linux_arm64_sha256": "stale-source-hash",
        }
        notes = self.module.claude_notes(manifest, versions, "toolchain-test")
        self.assertIn("new-source-hash", notes)
        self.assertNotIn("stale-source-hash", notes)
        self.assertIn("make build VERSION=9.8.7", notes)


if __name__ == "__main__":
    unittest.main()
