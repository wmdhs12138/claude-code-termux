""".github/: workflow wiring, release notes and the toolchain fingerprint."""
import importlib.util
import subprocess
import unittest

from common import ROOT, WORKFLOW, BIONIC_CI


class ReleaseNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / ".github" / "release_notes.py"
        spec = importlib.util.spec_from_file_location("release_notes", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_uses_manifest_source_hash_and_short_build_command(self):
        manifest = {
            "claude": "9.8.7",
            "claude_linux_arm64_sha256": "new-source-hash",
            "output_sha256": "output-hash",
            "base_bun": {},
        }
        notes = self.module.claude_notes(manifest, "toolchain-test")
        self.assertIn("new-source-hash", notes)
        self.assertIn("output-hash", notes)
        self.assertIn("scripts/build.sh 9.8.7", notes)
        self.assertIn("build-manifest.json", notes)
        self.assertNotIn("Graft 结构自检", notes)

    def test_reports_bionic_acceptance(self):
        manifest = {
            "claude": "9.8.7",
            "output_version": "9.8.7 (Claude Code)",
            "output_sha256": "output-hash",
            "base_bun": {},
            "tui_smoke": {"ran": True, "result": "pass"},
            "ci_acceptance": {
                "runtime": "termux-docker/bionic",
                "architecture": "aarch64",
                "version_probe": "pass",
                "tui_smoke": "pass",
            },
        }
        notes = self.module.claude_notes(manifest, "toolchain-test")
        self.assertIn("Bionic AArch64 直接运行、版本探针和 PTY/TUI 渲染通过", notes)
        self.assertNotIn("仅完成结构校验", notes)

    def test_toolchain_notes_focus_on_changes(self):
        notes = self.module.toolchain_notes(
            "toolchain-v23-abcdef0", "abcdef0", "toolchain-v22-1234567",
            "abc1234 refine release notes", {"base_bun": {"version": "1.4.3"}},
        )
        self.assertIn("abc1234 refine release notes", notes)
        self.assertIn("Bun `1.4.3`", notes)
        self.assertNotIn("已发布过 release 的 Claude 版本", notes)
        self.assertLess(len(notes), 400)


class FingerprintTests(unittest.TestCase):
    FINGERPRINT = ROOT / "scripts" / "fingerprint.sh"

    def test_prints_seven_hex_digits(self):
        if not (ROOT / ".git").exists():
            self.skipTest("not a git checkout")
        proc = subprocess.run(["bash", str(self.FINGERPRINT)], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout.strip(), r"^[0-9a-f]{7}$")

    def test_covers_every_build_input(self):
        proc = subprocess.run(["bash", str(self.FINGERPRINT), "--paths"],
                              text=True, capture_output=True)
        paths = proc.stdout.split()
        for path in ("install.sh", "versions.json", "scripts", "tools", "runtime", ".github"):
            self.assertIn(path, paths)

    def test_ci_uses_the_script_instead_of_a_copy(self):
        workflow = WORKFLOW.read_text()
        self.assertIn('TC_FP="$(scripts/fingerprint.sh)"', workflow)
        self.assertIn('TC_PATHS="$(scripts/fingerprint.sh --paths)"', workflow)
        self.assertNotIn("git ls-files", workflow)


class BionicCIWiringTests(unittest.TestCase):
    def test_upgrades_termux_before_installing_dependencies(self):
        script = BIONIC_CI.read_text()
        self.assertLess(script.index("pkg update -y"), script.index("pkg upgrade -y"))
        self.assertLess(script.index("pkg upgrade -y"), script.index("pkg install -y"))

    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()
        cls.script = BIONIC_CI.read_text()

    def test_uses_pinned_termux_image_on_native_arm_runner(self):
        self.assertIn("runs-on: ubuntu-24.04-arm", self.workflow)
        self.assertRegex(
            self.workflow,
            r"termux/termux-docker@sha256:[0-9a-f]{64}",
        )
        self.assertIn("docker run --rm --privileged", self.workflow)

    def test_bionic_path_executes_candidate_and_requires_tui(self):
        self.assertNotIn("SKIP_RUN", self.script)
        self.assertIn('bash scripts/build.sh "$VERSION"', self.script)
        self.assertIn('./dist/claude --version', self.script)
        self.assertIn('smoke.get("ran") is True', self.script)
        self.assertIn('smoke.get("result") == "pass"', self.script)

    def test_binary_is_not_selected_for_artifact_upload(self):
        upload = self.workflow.split(
            "- name: Upload Bionic reports (no binary)", 1
        )[1].split("\n  release:", 1)[0]
        self.assertNotIn("dist/claude\n", upload)
        self.assertIn("dist/build-manifest.json", upload)

    def test_toolchain_release_attaches_accepted_manifest(self):
        release = self.workflow.split("- name: Cut toolchain release", 1)[1].split(
            "- name: Cut Claude Code release", 1
        )[0]
        self.assertIn('MANIFEST=reports/dist/build-manifest.json', release)
        self.assertIn('--latest=false --notes-file tc-notes.md "$MANIFEST"', release)


if __name__ == "__main__":
    unittest.main()
