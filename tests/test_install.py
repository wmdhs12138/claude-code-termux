"""install.sh, scripts/install-approved.sh and the retired launcher stub."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from common import ROOT, UPDATE, INSTALL, INSTALL_APPROVED


class InstallerTests(unittest.TestCase):
    def test_updates_dependencies_even_when_all_commands_are_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(INSTALL, root / "install.sh")
            (scripts / "build.sh").write_text("exit 39\n")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in {
                "rg": "exit 0\n",
                "pkg": 'echo "$*" >> "$PACKAGE_LOG"\n',
            }.items():
                command = bin_dir / name
                command.write_text("#!/bin/sh\n" + body)
                command.chmod(0o755)
            log = root / "packages.log"
            env = dict(os.environ)
            env.update({
                "PATH": f"{bin_dir}:{env['PATH']}",
                "PREFIX": "",
                "PACKAGE_LOG": str(log),
                "CLAUDE_CODE_TERMUX_ALLOW_UNSUPPORTED": "1",
                "CLAUDE_CODE_TERMUX_SKIP_DEPS": "0",
            })
            proc = subprocess.run(
                ["bash", str(root / "install.sh"), "1.2.3"],
                text=True, capture_output=True, env=env,
            )
            calls = log.read_text().splitlines()
        self.assertEqual(proc.returncode, 39, proc.stdout + proc.stderr)
        self.assertEqual(calls[0], "update -y")
        self.assertTrue(calls[1].startswith("upgrade -y "))
        self.assertIn("--force-confdef", calls[1])
        self.assertIn("--force-confold", calls[1])
        self.assertEqual(len(calls), 3)
        self.assertIn("python", calls[2])
        self.assertIn("ripgrep", calls[2])

    def test_refreshes_package_index_and_explains_dependency_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in {
                "rg": "exit 127\n",
                "pkg": 'echo "$*" >> "$PACKAGE_LOG"\n'
                       'if [ "$1" = install ]; then exit 42; fi\n',
            }.items():
                command = root / name
                command.write_text("#!/bin/sh\n" + body)
                command.chmod(0o755)
            log = root / "packages.log"
            env = dict(os.environ)
            env.update({
                "PATH": f"{tmp}:{env['PATH']}",
                "PREFIX": "",
                "PACKAGE_LOG": str(log),
                "CLAUDE_CODE_TERMUX_ALLOW_UNSUPPORTED": "1",
                "CLAUDE_CODE_TERMUX_SKIP_DEPS": "0",
            })
            proc = subprocess.run(
                ["bash", str(INSTALL)], text=True, capture_output=True, env=env,
            )
            calls = log.read_text().splitlines()
        self.assertEqual(proc.returncode, 42, proc.stdout + proc.stderr)
        self.assertEqual(calls[0], "update -y")
        self.assertTrue(calls[1].startswith("upgrade -y "))
        self.assertTrue(calls[2].startswith("install -y "))
        self.assertIn("ripgrep", calls[2])
        self.assertIn("termux-change-repo", proc.stderr)

    def test_reports_ripgrep_when_path_command_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            rg = Path(tmp) / "rg"
            rg.write_text("#!/bin/sh\nexit 127\n")
            rg.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "PATH": f"{tmp}:{env['PATH']}",
                "PREFIX": "",
                "CLAUDE_CODE_TERMUX_ALLOW_UNSUPPORTED": "1",
                "CLAUDE_CODE_TERMUX_SKIP_DEPS": "1",
            })
            proc = subprocess.run(
                ["bash", str(INSTALL)], text=True, capture_output=True, env=env,
            )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("missing packages:", proc.stderr)
        self.assertIn("ripgrep", proc.stderr)

    def test_rejects_invalid_version_before_platform_or_dependency_checks(self):
        proc = subprocess.run(
            ["bash", str(INSTALL), "2.1.3/../../unexpected"],
            text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("invalid version", proc.stderr)

    def test_default_install_uses_approved_tag_and_temporary_build(self):
        installer = INSTALL.read_text()
        approved = INSTALL_APPROVED.read_text()
        self.assertIn('if [ "$VERSION" = "latest" ]; then', installer)
        self.assertIn('bash "$ROOT/scripts/install-approved.sh"', installer)
        self.assertIn('"$repo_url/releases/latest"', approved)
        self.assertIn('source_tag="$release_tag"', approved)
        self.assertIn('releases/download/$toolchain_tag/build-manifest.json', approved)
        self.assertIn('$tmp_root/claude-code-termux-install.XXXXXX', approved)
        self.assertIn('built_hashes" != "$expected_hashes', approved)
        self.assertIn('bash "$source_dir/install.sh" --no-build', approved)
        self.assertNotIn('refs/heads/main', approved)

    def test_default_install_rejects_unaccepted_release_manifest(self):
        source = INSTALL_APPROVED.read_text()
        script = source.split('python3 - "$path" "$version" "$require_acceptance" <<\'PY\'\n', 1)[1].split(
            "\nPY\n}", 1
        )[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            doc = {
                "claude": "2.1.281",
                "claude_linux_arm64_sha256": "a" * 64,
                "base_bun": {"archive_sha256": "b" * 64, "binary_sha256": "c" * 64},
                "tui_smoke": {"ran": True, "result": "pass"},
                "ci_acceptance": {
                    "runtime": "termux-docker/bionic",
                    "architecture": "aarch64",
                    "version_probe": "pass",
                    "tui_smoke": "pass",
                },
            }
            path.write_text(json.dumps(doc))
            args = [sys.executable, "-", str(path), "2.1.281", "1"]
            accepted = subprocess.run(args, input=script, text=True, capture_output=True)
            doc["ci_acceptance"]["tui_smoke"] = "fail"
            path.write_text(json.dumps(doc))
            rejected = subprocess.run(args, input=script, text=True, capture_output=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotEqual(rejected.returncode, 0)

    def test_defaults_to_termux_prefix_bin(self):
        source = INSTALL.read_text()
        self.assertIn(
            'INSTALL_DIR="${CLAUDE_CODE_TERMUX_INSTALL_DIR:-${PREFIX:-$HOME}/bin}"',
            source,
        )
        self.assertNotIn('default: ~/bin', source)

    def install_candidate(self, root, target_dir, version):
        candidate = root / "dist" / "claude"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INSTALL, root / "install.sh")
        candidate.write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
        candidate.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "CLAUDE_CODE_TERMUX_ALLOW_UNSUPPORTED": "1",
                "CLAUDE_CODE_TERMUX_INSTALL_DIR": str(target_dir),
            }
        )
        return subprocess.run(
            ["bash", str(root / "install.sh"), "--no-build"],
            text=True,
            capture_output=True,
            env=env,
        )

    def test_installs_direct_elf_atomically_and_backs_up_old_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project with spaces"
            target_dir = Path(tmp) / "bin with spaces"
            target_dir.mkdir()
            target = target_dir / "claude"
            target.write_text("old launcher\n")
            target.chmod(0o700)

            proc = self.install_candidate(root, target_dir, "9.8.7")

            backup = target_dir / ".claude.backup"
            leftovers = list(target_dir.glob(".claude-install.*"))
            target_text = target.read_text()
            backup_text = backup.read_text() if backup.exists() else None

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(target_text, "#!/bin/sh\necho '9.8.7 (Claude Code)'\n")
        self.assertEqual(backup_text, "old launcher\n")
        self.assertEqual(leftovers, [])
        self.assertIn("installed:", proc.stdout)

    def test_reinstall_keeps_one_backup_and_prunes_timestamped_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            target_dir = Path(tmp) / "bin"
            target_dir.mkdir()
            (target_dir / "claude").write_text("first\n")
            legacy = [
                target_dir / "claude.backup-20260916T010203Z",
                target_dir / "claude.backup-20260916T010203Z.1",
            ]
            for path in legacy:
                path.write_text("legacy\n")
            unrelated = target_dir / "claude.backup-notes.txt"
            unrelated.write_text("keep me\n")

            first = self.install_candidate(root, target_dir, "1.0.0")
            second = self.install_candidate(root, target_dir, "2.0.0")

            backups = sorted(p.name for p in target_dir.iterdir() if "backup" in p.name)
            backup_text = (target_dir / ".claude.backup").read_text()
            legacy_left = [path.exists() for path in legacy]
            unrelated_kept = unrelated.exists()

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("removed:   2 old claude.backup-* file(s)", first.stdout)
        self.assertNotIn("removed:", second.stdout)
        self.assertEqual(backups, [".claude.backup", "claude.backup-notes.txt"])
        self.assertEqual(backup_text, "#!/bin/sh\necho '1.0.0 (Claude Code)'\n")
        self.assertEqual(legacy_left, [False, False])
        self.assertTrue(unrelated_kept)


class RetiredUpdateTests(unittest.TestCase):
    def test_retired_launcher_update_points_to_installer_without_building(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project with spaces"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(UPDATE, scripts / "update.sh")
            marker = Path(tmp) / "network-or-build"
            for name in ("curl", "git"):
                tool = Path(tmp) / name
                tool.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
                tool.chmod(0o755)
            (scripts / "build.sh").write_text(f'touch "{marker}"\n')
            env = dict(os.environ)
            env["PATH"] = f"{tmp}:{env['PATH']}"
            proc = subprocess.run(
                ["bash", str(scripts / "update.sh"), "--force"],
                text=True, capture_output=True, env=env,
            )
            touched = marker.exists()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(f'cd "{root}" && ./install.sh', proc.stderr)
        self.assertFalse(touched)


if __name__ == "__main__":
    unittest.main()
