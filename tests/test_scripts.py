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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "tui_smoke.py"
FETCH = ROOT / "scripts" / "fetch-claude.sh"
BUILD = ROOT / "scripts" / "build.sh"
UPDATE = ROOT / "scripts" / "update.sh"
RUNTIME = ROOT / "runtime"
SELF_UPDATE = RUNTIME / "self-update.sh"
ASSEMBLE_RUNTIME = ROOT / "tools" / "assemble_runtime.py"
EMBED_PRELOAD = ROOT / "tools" / "embed_preload.py"
INSTALL = ROOT / "install.sh"
INSTALL_APPROVED = ROOT / "scripts" / "install-approved.sh"
ENSURE_BUN = ROOT / "scripts" / "ensure-bun-base.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
BIONIC_CI = ROOT / ".github" / "ci" / "bionic-build.sh"


class VersionValidationTests(unittest.TestCase):
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

    def test_embedded_update_enables_shared_bun_cache(self):
        source = SELF_UPDATE.read_text()
        self.assertIn(
            'CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE="$cache_root/bun-bases"', source
        )
        self.assertIn('scripts/ensure-bun-base.sh', BUILD.read_text())

    def test_embedded_update_uses_approved_releases_without_github_api_quota(self):
        source = SELF_UPDATE.read_text()
        self.assertIn('claude-code-termux/releases/latest', source)
        self.assertIn('releases/download/$release_tag/build-manifest.json', source)
        self.assertIn('releases/download/$toolchain_tag/build-manifest.json', source)
        self.assertIn('git ls-remote --tags https://github.com/wmdhs12138/claude-code-termux.git', source)
        self.assertNotIn('refs/heads/main', source)
        self.assertNotIn('claude-code-releases/latest', source)
        self.assertNotIn('api.github.com/repos/wmdhs12138/claude-code-termux/commits/main', source)
        self.assertIn('commands=(bash curl git python3', INSTALL.read_text())

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

    # 2.1.294 layout: the constructor sits alone in a shared chunk exporting a
    # factory, and the Ink chunk that calls the native members imports it.
    SPLIT_FACTORY = (
        b'function TRn(e,n){if(typeof Bun.ant?.CellSegmenter!=="function")throw Error("x");'
        b"return new Bun.ant.CellSegmenter({substitute:e,screen:n})}var lvt=16384;export{lvt,TRn};"
    )
    SPLIT_INK = (
        b'import{lvt,TRn}from"/$bunfs/root/chunk-factory.js";'
        b"class _d{native=TRn(a,b);a=this.native.graphemes;b=this.native.sgrKeys;"
        b"c=this.native.sgrCloseKeys;d=this.native.uris;"
        b"e(){this.native.segment(1,2,3);this.native.paint(1,2,3)}f(){L0.setCell(1,2,3)}}"
    )

    def test_follows_constructor_hoisted_into_a_shared_chunk(self):
        report = self.module.analyze(self.graph([
            ("/$bunfs/root/chunk-factory.js", self.SPLIT_FACTORY),
            ("/$bunfs/root/chunk-ink.js", self.SPLIT_INK),
        ]))
        self.assertEqual(report["modules"], ["/$bunfs/root/chunk-factory.js"])
        self.assertEqual(report["consumer_modules"], ["/$bunfs/root/chunk-ink.js"])
        self.assertEqual(report["native_members"],
                         ["graphemes", "paint", "segment", "sgrCloseKeys", "sgrKeys", "uris"])
        self.assertTrue(report["set_cell"])

    def test_rejects_new_native_member_in_an_importing_chunk(self):
        ink = self.SPLIT_INK.replace(b"f(){", b"m(){this.native.measure()}f(){")
        with self.assertRaisesRegex(self.module.DriftError, "measure"):
            self.module.analyze(self.graph([
                ("/$bunfs/root/chunk-factory.js", self.SPLIT_FACTORY),
                ("/$bunfs/root/chunk-ink.js", ink),
            ]))

    def test_ignores_modules_that_import_only_the_shared_constants(self):
        # In 2.1.294 ~350 modules import the factory chunk for its constants,
        # and the graph has an unrelated `.native.test`. Matching on the chunk
        # path instead of the factory would scan all of them and invite a
        # false drift.
        report = self.module.analyze(self.graph([
            ("/$bunfs/root/chunk-factory.js", self.SPLIT_FACTORY),
            ("/$bunfs/root/chunk-ink.js", self.SPLIT_INK),
            ("/$bunfs/root/chunk-other.js",
             b'import{lvt}from"/$bunfs/root/chunk-factory.js";x.native.test(lvt)'),
        ]))
        self.assertEqual(report["consumer_modules"], ["/$bunfs/root/chunk-ink.js"])
        self.assertNotIn("test", report["native_members"])

    def test_follows_a_renamed_factory_export(self):
        factory = self.SPLIT_FACTORY.replace(b"export{lvt,TRn}", b"export{lvt,TRn as Mk}")
        ink = self.SPLIT_INK.replace(b"import{lvt,TRn}", b"import{lvt,Mk as TRn}")
        report = self.module.analyze(self.graph([
            ("/$bunfs/root/chunk-factory.js", factory),
            ("/$bunfs/root/chunk-ink.js", ink),
        ]))
        self.assertEqual(report["consumer_modules"], ["/$bunfs/root/chunk-ink.js"])


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

def assemble_runtime(out_dir):
    out = Path(out_dir) / "runtime.js"
    proc = subprocess.run([sys.executable, str(ASSEMBLE_RUNTIME), str(RUNTIME), str(out)],
                          text=True, capture_output=True)
    if proc.returncode:
        raise AssertionError(proc.stdout + proc.stderr)
    return out


class RuntimeTests(unittest.TestCase):
    def test_file_implements_the_native_surface(self):
        source = (RUNTIME / "40-cell-segmenter.js").read_text()
        for member in ("Bun.ant.CellSegmenter =", "segment(text, cells, runs", "paint(", "setCell("):
            self.assertIn(member, source)

    def test_syntax_is_valid(self):
        for runtime in ("node", "bun"):
            exe = shutil.which(runtime)
            if exe:
                break
        else:
            self.skipTest("no JS runtime available for a syntax check")
        with tempfile.TemporaryDirectory() as tmp:
            for path in [*sorted(RUNTIME.glob("*.js")), assemble_runtime(tmp)]:
                proc = subprocess.run([exe, "--check", str(path)], text=True, capture_output=True)
                self.assertEqual(proc.returncode, 0, f"{path.name}: {proc.stdout}{proc.stderr}")

    def test_assembly_inlines_the_updater_and_keeps_module_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = assemble_runtime(tmp).read_text()
        self.assertNotIn("__include__", source)
        self.assertIn(json.dumps(SELF_UPDATE.read_text()), source)
        order = [source.index(f"// runtime/{p.name}\n") for p in sorted(RUNTIME.glob("*.js"))]
        self.assertEqual(order, sorted(order))
        # The updater exits the process, so it must run before the shims load.
        self.assertLess(source.index("// runtime/20-self-update.js"),
                        source.index("// runtime/30-peer-credentials.js"))

    def test_assembly_rejects_a_missing_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            (runtime / "10-x.js").write_text('var s = __include__("missing.sh");')
            proc = subprocess.run(
                [sys.executable, str(ASSEMBLE_RUNTIME), str(runtime), str(Path(tmp) / "out.js")],
                text=True, capture_output=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing.sh", proc.stderr)

    def test_embeds_direct_exec_android_defaults(self):
        source = (RUNTIME / "10-android-defaults.js").read_text()
        self.assertIn('process.platform !== "android"', source)
        self.assertIn('process.env.USE_BUILTIN_RIPGREP === undefined', source)
        self.assertIn('process.env.DISABLE_AUTOUPDATER === undefined', source)

    def test_provides_peer_credentials_without_overriding_native(self):
        source = (RUNTIME / "30-peer-credentials.js").read_text()
        self.assertIn('process.getBuiltinModule("bun:ffi")', source)
        self.assertIn("var SO_PEERCRED = 17;", source)
        self.assertIn('if (typeof Bun.ant.getPeerPid !== "function")', source)
        self.assertIn('if (typeof Bun.ant.getPeerUid !== "function")', source)
        # Its own module, so a runtime that ships CellSegmenter natively does
        # not skip the peer shims through the CellSegmenter early return.
        self.assertNotIn("CellSegmenter", source)

    def test_peer_credentials_read_the_other_process(self):
        candidates = [shutil.which("bun"), ROOT / "work" / "bun-android" / "bun"]
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        candidates += sorted(cache.glob("claude-code-termux/self-update/bun-bases/bun-*/bun"))
        bun = next((str(c) for c in candidates if c and os.access(c, os.X_OK)), None)
        if bun is None:
            self.skipTest("no Bun runtime available")
        harness = r"""
import { connect } from "net";
if (process.platform !== "android") { console.log("SKIP"); process.exit(0); }
const path = process.argv[2];
const child = Bun.spawn([process.argv[3], "-c", `
import os, socket, sys
s = socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen(1)
print("ready", flush=True)
c, _ = s.accept(); c.recv(1)
`, path], { stdout: "pipe" });
await child.stdout.getReader().read();
const c = connect({ path });
c.on("connect", () => {
  const fd = c._handle.fd;
  let bad;
  try { Bun.ant.getPeerUid(-1); } catch (e) { bad = e.message; }
  console.log(JSON.stringify({ pid: Bun.ant.getPeerPid(fd), uid: Bun.ant.getPeerUid(fd),
                               child: child.pid, self: process.getuid(), bad }));
  c.end("x");
});
await child.exited;
"""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "peer.mjs"
            script.write_text(assemble_runtime(tmp).read_text() + harness)
            proc = subprocess.run([bun, str(script), str(Path(tmp) / "peer.sock"), sys.executable],
                                  text=True, capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        if proc.stdout.strip() == "SKIP":
            self.skipTest("Bun runtime is not Android")
        result = json.loads(proc.stdout)
        self.assertEqual(result["pid"], result["child"])
        self.assertEqual(result["uid"], result["self"])
        self.assertIn("not a socket fd", result["bad"])

    def test_embeds_atomic_self_update(self):
        self.assertIn('argv[ai] === "update" || argv[ai] === "upgrade"',
                      (RUNTIME / "20-self-update.js").read_text())
        source = (RUNTIME / "20-self-update.js").read_text() + SELF_UPDATE.read_text()
        self.assertIn('claude-code-termux/releases/latest', source)
        self.assertIn('source_tag="$release_tag"', source)
        self.assertIn('built_hashes" != "$expected_hashes', source)
        self.assertIn('mv -f "$replacement" "$target"', source)
        self.assertIn('updateArgs.indexOf("--check")', source)
        self.assertIn('updateArgs.indexOf("--force")', source)

    def test_embedded_update_shell_syntax(self):
        proc = subprocess.run(["bash", "-n", str(SELF_UPDATE)], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_approved_manifest_requires_bionic_acceptance_and_matching_inputs(self):
        source = SELF_UPDATE.read_text()
        script = source.split("python3 -c '\n", 1)[1].split(
            "\n' \"$latest\" \"$require_acceptance\"", 1
        )[0]
        digest = "a" * 64
        manifest = {
            "claude": "2.1.281",
            "claude_linux_arm64_sha256": digest,
            "base_bun": {"archive_sha256": "b" * 64, "binary_sha256": "c" * 64},
            "tui_smoke": {"ran": True, "result": "pass"},
            "ci_acceptance": {
                "runtime": "termux-docker/bionic",
                "architecture": "aarch64",
                "version_probe": "pass",
                "tui_smoke": "pass",
            },
        }

        def validate(doc):
            return subprocess.run(
                [sys.executable, "-c", script, "2.1.281", "1"],
                input=json.dumps(doc), text=True, capture_output=True,
            )

        good = validate(manifest)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(good.stdout.strip(), f"{digest} {'b' * 64} {'c' * 64}")
        for edit in (
            {"ci_acceptance": {}},
            {"tui_smoke": {"ran": True, "result": "fail"}},
            {"claude_linux_arm64_sha256": "bad"},
        ):
            rejected = validate({**manifest, **edit})
            self.assertNotEqual(rejected.returncode, 0)

    def test_self_update_uses_termux_tmp_and_only_persists_bun(self):
        source = SELF_UPDATE.read_text()
        self.assertIn('printenv TMPDIR', source)
        self.assertIn('$tmp_root/claude-code-termux-update.XXXXXX', source)
        self.assertIn('CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE="$cache_root/bun-bases"', source)
        self.assertIn('$cache_root/active-bun-sha256', source)
        self.assertIn('trap cleanup_update EXIT', source)
        self.assertNotIn('CLAUDE_CODE_TERMUX_CACHE_KEEP', source)
        self.assertNotIn('source_dir="$cache_root/claude-', source)

    def test_self_update_check_is_read_only(self):
        source = SELF_UPDATE.read_text()
        check_exit = source.index('if [ "$check" = "1" ]')
        cache_root = source.index('cache_root="$cache_base/claude-code-termux/self-update"')
        tmp_dir = source.index('stage="$(mktemp -d "$tmp_root/')
        self.assertLess(check_exit, cache_root)
        self.assertLess(cache_root, tmp_dir)

    def test_legacy_claude_cache_cleanup_preserves_unrelated_paths(self):
        source = SELF_UPDATE.read_text()
        marker = 'python3 - "$root" <<\'PY\'\n'
        script = source.split(marker, 1)[1].split("\nPY\n}", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victims = [
                "toolchain-" + "1" * 40,
                "claude-2.1.278-toolchain-" + "2" * 40,
                "download.abcd",
                "update.efgh",
            ]
            for name in victims:
                path = root / name
                path.mkdir()
                (path / "payload").write_bytes(b"x")
            unrelated = root / "keep-me"
            unrelated.mkdir()
            link = root / ("toolchain-" + "a" * 40)
            link.symlink_to(root / victims[0], target_is_directory=True)

            proc = subprocess.run(
                [sys.executable, "-", str(root)], input=script, text=True, capture_output=True
            )
            remaining = {path.name for path in root.iterdir()}

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(set(victims).isdisjoint(remaining))
        self.assertIn("keep-me", remaining)
        self.assertIn(link.name, remaining)
        self.assertIn("4 removed", proc.stdout)

    def test_bun_cache_pruner_keeps_only_current_base_by_default(self):
        source = SELF_UPDATE.read_text()
        marker = 'python3 - "$root/bun-bases" "$protected_sha" "$bun_keep" <<\'PY\'\n'
        script = source.split(marker, 1)[1].split("\nPY\n}", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bun-bases"
            root.mkdir()
            current_payload = b"current-bun"
            current_sha = hashlib.sha256(current_payload).hexdigest()
            payloads = [current_payload, b"previous-bun", b"oldest-bun"]
            names = []
            for index, payload in enumerate(payloads, start=1):
                digest = hashlib.sha256(payload).hexdigest()
                path = root / f"bun-{digest}"
                path.mkdir(parents=True)
                (path / "bun").write_bytes(payload)
                os.utime(path, (index, index))
                names.append(path.name)

            proc = subprocess.run(
                [sys.executable, "-", str(root), current_sha, "1"],
                input=script,
                text=True,
                capture_output=True,
            )
            remaining = {path.name for path in root.iterdir()}

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(names[0], remaining)  # protected current, despite oldest mtime
        self.assertNotIn(names[1], remaining)
        self.assertNotIn(names[2], remaining)
        self.assertIn("Bun cache cleanup: 1 retained, 2 removed", proc.stdout)


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
