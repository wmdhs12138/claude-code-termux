import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "tui_smoke.py"
FETCH = ROOT / "scripts" / "fetch-claude.sh"
BUILD = ROOT / "scripts" / "build.sh"
UPDATE = ROOT / "scripts" / "update.sh"
LAUNCHER = ROOT / "scripts" / "launcher.sh"
POLYFILL = ROOT / "tools" / "cellsegmenter-polyfill.js"
EMBED_PRELOAD = ROOT / "tools" / "embed_preload.py"


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


class GraphAdaptationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tools" / "adapt_graph.py"
        spec = importlib.util.spec_from_file_location("adapt_graph", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_matches_search_opt_in_getter_across_minified_names(self):
        for getter in (
            b"function KKn(){return n().host.launchOptions.searchToolsOptIn()}",
            b"function $Xn(){return n().host.launchOptions.searchToolsOptIn()}",
        ):
            matches = list(self.module.SEARCH_OPT_IN_GETTER.finditer(getter))
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].group(0), getter)

    def test_does_not_match_unrelated_search_tools_text(self):
        data = b"searchToolsOptIn(){return this.#C}"
        self.assertEqual(list(self.module.SEARCH_OPT_IN_GETTER.finditer(data)), [])


class NativeAbiCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tools" / "check_native_abi.py"
        spec = importlib.util.spec_from_file_location("check_native_abi", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @staticmethod
    def graph(sources):
        payload = bytearray()
        records = []
        for name, src in sources:
            src_off = len(payload)
            payload += src
            name_bytes = name.encode()
            name_off = len(payload)
            payload += name_bytes
            records.append((name_off, len(name_bytes), src_off, len(src)))
        mod_off = len(payload)
        for rec in records:
            payload += struct.pack("<13I", *rec, *([0] * 9))
        payload += struct.pack("<QIIIIII", len(payload), mod_off, len(records) * 52, 0, 0, 0, 0)
        payload += b"\n---- Bun! ----\n"
        return bytes(payload)

    def test_accepts_the_surface_the_polyfill_implements(self):
        src = (
            b'function As(n){if(typeof Bun.ant?.CellSegmenter!=="function")throw Error("x");'
            b"return new Bun.ant.CellSegmenter({})}"
            b"class _d{native=As(aXe);a=this.native.graphemes;b=this.native.sgrKeys;"
            b"c=this.native.sgrCloseKeys;d=this.native.uris;"
            b"e(){this.native.segment(1,2,3);this.native.paint(1,2,3)}"
            b"f(){L0.setCell(1,2,3)}g(){return typeof Bun.ant?.getPeerPid}}"
        )
        report = self.module.analyze(self.graph([("/$bunfs/root/chunk-ink.js", src)]))
        self.assertTrue(report["required"])
        self.assertEqual(report["native_members"],
                         ["graphemes", "paint", "segment", "sgrCloseKeys", "sgrKeys", "uris"])
        self.assertTrue(report["set_cell"])
        self.assertTrue(report["token_seen"])

    def test_ignores_graphs_that_never_construct_cell_segmenter(self):
        src = b'if(typeof Bun.ant?.getPeerPid==="function")x();'
        report = self.module.analyze(self.graph([("/$bunfs/root/chunk-a.js", src)]))
        self.assertFalse(report["required"])
        self.assertFalse(report["token_seen"])
        self.assertIn("getPeerPid", report["bun_ant_members"])

    def test_rejects_cell_segmenter_under_an_unrecognized_spelling(self):
        # A destructured or renamed reference hides every call site from the
        # Bun.ant.* regexes. Without the token_seen guard this reported
        # required=False and waved the build through, silently disabling the
        # whole check -- so it has to fail instead.
        src = b"const {CellSegmenter}=Bun.ant;new CellSegmenter({});x=this.native.segment"
        with self.assertRaisesRegex(self.module.DriftError, "never as Bun.ant.CellSegmenter"):
            self.module.analyze(self.graph([("/$bunfs/root/chunk-ink.js", src)]))

    def test_token_elsewhere_does_not_mask_a_real_match(self):
        # The guard must fire only when the strict spelling is absent. A graph
        # that mentions the token in passing *and* constructs the real thing is
        # an ordinary 2.1.271+ graph and has to keep building.
        strict = (
            b"new Bun.ant.CellSegmenter({});"
            b"x=this.native.segment;y=this.native.paint;a=this.native.graphemes;"
            b"b=this.native.sgrKeys;c=this.native.sgrCloseKeys;d=this.native.uris"
        )
        report = self.module.analyze(
            self.graph(
                [
                    ("/$bunfs/root/chunk-ink.js", strict),
                    ("/$bunfs/root/chunk-notes.js", b"// CellSegmenter is declared by the runtime"),
                ]
            )
        )
        self.assertTrue(report["required"])
        self.assertTrue(report["token_seen"])

    def test_rejects_unknown_bun_ant_interface(self):
        src = b'new Bun.ant.CellSegmenter({}); Bun.ant.TerminalReader'
        with self.assertRaisesRegex(self.module.DriftError, "TerminalReader"):
            self.module.analyze(self.graph([("/$bunfs/root/chunk-ink.js", src)]))

    def test_rejects_new_native_member(self):
        src = (
            b'new Bun.ant.CellSegmenter({});'
            b"x=this.native.segment;y=this.native.paint;a=this.native.graphemes;"
            b"b=this.native.sgrKeys;c=this.native.sgrCloseKeys;d=this.native.uris;"
            b"z=this.native.measure"
        )
        with self.assertRaisesRegex(self.module.DriftError, "measure"):
            self.module.analyze(self.graph([("/$bunfs/root/chunk-ink.js", src)]))

    def test_rejects_missing_native_member(self):
        src = (
            b'new Bun.ant.CellSegmenter({});'
            b"x=this.native.segment;y=this.native.paint;"
            b"a=this.native.graphemes;b=this.native.sgrKeys;c=this.native.sgrCloseKeys"
        )
        with self.assertRaisesRegex(self.module.DriftError, "uris"):
            self.module.analyze(self.graph([("/$bunfs/root/chunk-ink.js", src)]))


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


class CellSegmenterPolyfillTests(unittest.TestCase):
    def test_file_implements_the_native_surface(self):
        source = POLYFILL.read_text()
        for member in ("Bun.ant.CellSegmenter =", "segment(text, cells, runs", "paint(", "setCell("):
            self.assertIn(member, source)

    def test_syntax_is_valid(self):
        for runtime in ("node", "bun"):
            exe = shutil.which(runtime)
            if exe:
                break
        else:
            self.skipTest("no JS runtime available for a syntax check")
        proc = subprocess.run([exe, "--check", str(POLYFILL)], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_embeds_direct_exec_android_defaults(self):
        source = POLYFILL.read_text()
        self.assertIn('process.platform === "android"', source)
        self.assertIn('process.env.USE_BUILTIN_RIPGREP === undefined', source)
        self.assertIn('process.env.DISABLE_AUTOUPDATER === undefined', source)

    def test_embeds_atomic_self_update(self):
        source = POLYFILL.read_text()
        self.assertIn('argv[ai] === "update" || argv[ai] === "upgrade"', source)
        self.assertIn('https://downloads.claude.ai/claude-code-releases/latest', source)
        self.assertIn('claude-code-termux/commits/main', source)
        self.assertIn('mv -f "$replacement" "$target"', source)
        self.assertIn('updateArgs.indexOf("--check")', source)
        self.assertIn('updateArgs.indexOf("--force")', source)


class EmbeddedPreloadTests(unittest.TestCase):
    @staticmethod
    def graph():
        payload = bytearray()
        records = []
        for name, source in ((b"dep", b"export default 1"), (b"entry", b"console.log('entry')")):
            source_at = len(payload)
            payload += source
            name_at = len(payload)
            payload += name
            records.append((name_at, len(name), source_at, len(source)))
        modules_at = len(payload)
        for name_at, name_len, source_at, source_len in records:
            payload += struct.pack(
                "<13I", name_at, name_len, source_at, source_len, *([0] * 9)
            )
        payload += struct.pack(
            "<QIIIIII", len(payload), modules_at, len(records) * 52, 1, 0, 0, 0
        )
        payload += b"\n---- Bun! ----\n"
        return bytes(payload)

    def test_embeds_preload_in_entry_and_repairs_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = root / "graph.bin"
            preload = root / "preload.js"
            output = root / "output.bin"
            report = root / "report.json"
            graph.write_bytes(self.graph())
            preload.write_text("globalThis.compat = true;")
            proc = subprocess.run(
                [sys.executable, str(EMBED_PRELOAD), str(graph), str(preload),
                 str(output), "--report", str(report)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = output.read_bytes()
            offsets_at = len(data) - 48
            byte_count, modules_at, modules_len, entry, *_ = struct.unpack_from(
                "<QIIIIII", data, offsets_at
            )
            self.assertEqual(byte_count, offsets_at)
            self.assertEqual(modules_len, 104)
            self.assertEqual(entry, 1)
            entry_record = modules_at + entry * 52
            source_at, source_len = struct.unpack_from("<II", data, entry_record + 8)
            source = data[source_at:source_at + source_len]
            self.assertTrue(source.startswith(b"globalThis.compat = true;"))
            self.assertTrue(source.endswith(b"console.log('entry')"))
            self.assertEqual(struct.unpack_from("<II", data, entry_record + 24), (0, 0))
            self.assertFalse(json.loads(report.read_text())["runtime_external_preload_required"])


class LauncherDirectTests(unittest.TestCase):
    def install_launcher(self, root, with_polyfill=True):
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        launcher = scripts / "launcher.sh"
        launcher.write_text(LAUNCHER.read_text().replace("@ROOT@", str(root)))
        launcher.chmod(0o755)
        if with_polyfill:
            tools = root / "tools"
            tools.mkdir()
            (tools / "cellsegmenter-polyfill.js").write_text("// stub\n")
        binary = root / "dist" / "claude"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\necho "BUN_OPTIONS=${BUN_OPTIONS:-unset}"\necho "args=$*"\n')
        binary.chmod(0o755)
        return launcher

    def run_launcher(self, launcher, env=None):
        base = {
            "PATH": os.environ["PATH"],
            "HOME": str(launcher.parents[1] / "home"),
        }
        Path(base["HOME"]).mkdir(exist_ok=True)
        if env:
            base.update(env)
        return subprocess.run(
            ["bash", str(launcher), "--version"], text=True, capture_output=True, env=base
        )

    def test_runs_grafted_binary_without_external_preload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            launcher = self.install_launcher(root)
            proc = self.run_launcher(launcher)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("BUN_OPTIONS=unset", proc.stdout)
        self.assertIn("args=--version", proc.stdout)

    def test_keeps_user_bun_options_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            launcher = self.install_launcher(root)
            proc = self.run_launcher(launcher, {"BUN_OPTIONS": "--smol"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("BUN_OPTIONS=--smol", proc.stdout)

    def test_launcher_does_not_require_polyfill_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            launcher = self.install_launcher(root, with_polyfill=False)
            proc = self.run_launcher(launcher)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("BUN_OPTIONS=unset", proc.stdout)


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
        self.assertNotIn("--preload $POLYFILL", section)
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
