"""runtime/: the JavaScript and shell embedded into the entry module."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from common import ROOT, BUILD, RUNTIME, SELF_UPDATE, ASSEMBLE_RUNTIME, INSTALL


def assemble_runtime(out_dir):
    out = Path(out_dir) / "runtime.js"
    proc = subprocess.run([sys.executable, str(ASSEMBLE_RUNTIME), str(RUNTIME), str(out)],
                          text=True, capture_output=True)
    if proc.returncode:
        raise AssertionError(proc.stdout + proc.stderr)
    return out


class RuntimeModuleTests(unittest.TestCase):
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
        # Any Bionic Bun will do: one on PATH, this checkout's build cache, or
        # the cache `claude update` keeps.
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        candidates = [shutil.which("bun"), *sorted(ROOT.glob("work/bun-bases/bun-*/bun")),
                      *sorted(cache.glob("claude-code-termux/self-update/bun-bases/bun-*/bun"))]
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


class SelfUpdateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
