""".github/: workflow wiring, release logic and release notes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from common import ROOT, WORKFLOW, BIONIC_CI

MANIFEST = {
    "claude": "1.2.4",
    "claude_linux_arm64_sha256": "source-hash",
    "output_sha256": "output-hash",
    "base_bun": {"version": "1.4.3"},
    "tui_smoke": {"ran": True, "result": "pass"},
    "ci_acceptance": {
        "runtime": "termux-docker/bionic",
        "architecture": "aarch64",
        "version_probe": "pass",
        "tui_smoke": "pass",
    },
}


def run_block(header):
    """The shell of the workflow step whose first line is `header`."""
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == header)
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
         *args], cwd=repo, text=True, capture_output=True, check=True,
    ).stdout.strip()


def commit(repo, path, subject):
    target = Path(repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(subject)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


class ReleaseNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / ".github" / "release_notes.py"
        spec = importlib.util.spec_from_file_location("release_notes", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_credentials_build_command_and_changes(self):
        notes = self.module.release_notes(MANIFEST, "v1.2.4", "c" * 40, "v1.2.3",
                                          "abc1234 runtime: change")
        self.assertIn("source-hash", notes)
        self.assertIn("output-hash", notes)
        self.assertIn("git checkout v1.2.4 && scripts/build.sh 1.2.4", notes)
        self.assertIn("`cccccccccccc`", notes)
        self.assertIn("相对 `v1.2.3` 的工具链变更", notes)
        self.assertIn("abc1234 runtime: change", notes)
        self.assertNotIn("重新发布", notes)

    def test_reports_bionic_acceptance(self):
        notes = self.module.release_notes(MANIFEST, "v1.2.4", "c" * 40, "-", "")
        self.assertIn("Bionic AArch64 直接运行、版本探针和 PTY/TUI 渲染通过", notes)
        self.assertNotIn("仅完成结构校验", notes)
        self.assertIn("（无）", notes)
        unaccepted = {k: v for k, v in MANIFEST.items() if k != "ci_acceptance"}
        self.assertIn("仅完成结构校验", self.module.release_notes(unaccepted, "v1.2.4", "c", "-", ""))

    def test_recut_says_what_changed(self):
        notes = self.module.release_notes(MANIFEST, "v1.2.4-r2", "c" * 40, "v1.2.4-r1", "x")
        self.assertIn("第 2 次重新发布", notes)


class ResolveTests(unittest.TestCase):
    def resolve(self, tags, requested, recut):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q")
            commit(repo, "a", "a")
            for tag in tags:
                git(repo, "tag", tag)
            output = Path(repo) / "github-output"
            env = dict(os.environ, REQUESTED=requested, RECUT=recut, GITHUB_OUTPUT=str(output))
            proc = subprocess.run(["bash", "-c", run_block("- name: Resolve requested Claude Code version")],
                                  cwd=repo, env=env, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return dict(line.split("=", 1) for line in output.read_text().split())

    def test_new_version_is_released_as_plain_tag(self):
        out = self.resolve(["v1.2.3"], "1.2.4", "false")
        self.assertEqual((out["tag"], out["unreleased"]), ("v1.2.4", "true"))

    def test_released_version_is_not_released_again(self):
        out = self.resolve(["v1.2.3"], "1.2.3", "false")
        self.assertEqual(out["unreleased"], "false")

    def test_recut_takes_the_next_free_suffix(self):
        out = self.resolve(["v1.2.3", "v1.2.3-r1"], "1.2.3", "true")
        self.assertEqual((out["tag"], out["unreleased"]), ("v1.2.3-r2", "true"))


class PublishTests(unittest.TestCase):
    """Runs the release job's shell against a scratch repository and a fake gh."""

    ASSETS = [line.strip().removeprefix('ASSETS="').rstrip('"')
              for line in run_block("- name: Publish release").splitlines()
              if line.strip().startswith(("reports/", 'ASSETS="reports/'))]

    def publish(self, tags, version, tag, fail_first=False):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            commit(repo, "runtime/x.js", "first")
            for t in tags:
                git(repo, "tag", t)
            commit(repo, "runtime/x.js", "runtime: change the shim")
            head = commit(repo, "docs/x.md", "docs: explain it")
            shutil.copytree(ROOT / ".github", repo / ".github")
            for asset in self.ASSETS:
                path = repo / asset
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            (repo / "reports/dist/build-manifest.json").write_text(json.dumps(MANIFEST))
            calls = Path(tmp) / "calls.jsonl"
            fake = Path(tmp) / "bin"
            fake.mkdir()
            (fake / "gh").write_text(f"""#!/usr/bin/env python3
import json, os, sys
calls = {str(calls)!r}
n = sum(1 for _ in open(calls)) if os.path.exists(calls) else 0
with open(calls, "a") as f:
    f.write(json.dumps({{"args": sys.argv[1:], "notes": open("claude-notes.md").read()}}) + "\\n")
if {fail_first!r} and n == 0:
    print("HTTP 422: tag_name was used by an immutable release", file=sys.stderr)
    sys.exit(1)
""")
            (fake / "gh").chmod(0o755)
            env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", GITHUB_SHA=head,
                       VERSION=version, TAG=tag)
            proc = subprocess.run(["bash", "-c", run_block("- name: Publish release")],
                                  cwd=repo, env=env, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return head, [json.loads(line) for line in calls.read_text().splitlines()]

    def test_pins_the_built_commit_and_lists_toolchain_changes(self):
        head, calls = self.publish(["v1.2.3"], "1.2.4", "v1.2.4")
        self.assertEqual(len(calls), 1)
        args, notes = calls[0]["args"], calls[0]["notes"]
        self.assertEqual(args[:3], ["release", "create", "v1.2.4"])
        self.assertEqual(args[args.index("--target") + 1], head)
        self.assertIn("--latest=true", args)
        self.assertIn("reports/dist/build-manifest.json", args)
        self.assertIn("runtime: change the shim", notes)
        self.assertNotIn("docs: explain it", notes)

    def test_recut_of_an_older_version_does_not_take_latest(self):
        _, calls = self.publish(["v1.2.3", "v1.2.5"], "1.2.3", "v1.2.3-r1")
        self.assertIn("--latest=false", calls[0]["args"])
        self.assertIn("第 1 次重新发布", calls[0]["notes"])

    def test_reserved_tag_falls_back_to_next_recut(self):
        _, calls = self.publish(["v1.2.3"], "1.2.4", "v1.2.4", fail_first=True)
        self.assertEqual([c["args"][2] for c in calls], ["v1.2.4", "v1.2.4-r1"])
        self.assertIn("第 1 次重新发布", calls[1]["notes"])

    def test_no_toolchain_releases(self):
        workflow = WORKFLOW.read_text()
        self.assertNotIn("toolchain-v", workflow)
        self.assertIn("if: needs.resolve.outputs.unreleased == 'true'", workflow)


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


if __name__ == "__main__":
    unittest.main()
