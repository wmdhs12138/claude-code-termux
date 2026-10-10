"""runtime/: the JavaScript and shell embedded into the entry module."""
import hashlib
import json
import math
import os
import pty
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
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
        self.assertIn('tag_ref="refs/tags/$release_tag"', source)
        self.assertIn('git ls-remote --tags https://github.com/wmdhs12138/claude-code-termux.git', source)
        # Releases carry their own toolchain; there are no toolchain tags to follow.
        self.assertNotIn('toolchain-v', source)
        self.assertNotIn('refs/heads/main', source)
        self.assertNotIn('claude-code-releases/latest', source)
        self.assertNotIn('api.github.com/repos/wmdhs12138/claude-code-termux/commits/main', source)
        self.assertIn('commands=(bash curl git python3', INSTALL.read_text())

    def test_embeds_atomic_self_update(self):
        self.assertIn('argv[ai] === "update" || argv[ai] === "upgrade"',
                      (RUNTIME / "20-self-update.js").read_text())
        source = (RUNTIME / "20-self-update.js").read_text() + SELF_UPDATE.read_text()
        self.assertIn('claude-code-termux/releases/latest', source)
        self.assertIn('tag_ref="refs/tags/$release_tag"', source)
        self.assertIn('"$built_hashes" != "$approved_hashes"', source)
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
            "output_sha256": "d" * 64,
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
        self.assertEqual(good.stdout.strip(), f"{digest} {'b' * 64} {'c' * 64} {'d' * 64}")
        for edit in (
            {"ci_acceptance": {}},
            {"tui_smoke": {"ran": True, "result": "fail"}},
            {"claude_linux_arm64_sha256": "bad"},
            {"output_sha256": "bad"},
        ):
            rejected = validate({**manifest, **edit})
            self.assertNotEqual(rejected.returncode, 0)

    def fake_github(self, tmp, release_tag, manifest_doc, build_script=None):
        """PATH entries that stand in for GitHub: releases/latest, the release
        manifest, `git ls-remote` and a toolchain tarball with `build_script`
        as its scripts/build.sh."""
        tmp = Path(tmp)
        manifest = tmp / "manifest.json"
        manifest.write_text(json.dumps(manifest_doc))
        commit = "e" * 40
        tarball = tmp / "toolchain.tar.gz"
        if build_script is not None:
            tree = tmp / "tree" / f"claude-code-termux-{commit}"
            (tree / "scripts").mkdir(parents=True)
            (tree / "scripts" / "build.sh").write_text(build_script)
            subprocess.run(["tar", "-czf", str(tarball), "-C", str(tmp / "tree"), tree.name],
                           check=True)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        release = f"https://github.com/wmdhs12138/claude-code-termux/releases/tag/{release_tag}"
        (fake_bin / "curl").write_text(f"""#!/bin/sh
case "$*" in
  *url_effective*) printf %s "{release}" ;;
  */archive/*)
    while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
    cp "{tarball}" "$out" ;;
  *) cat "{manifest}" ;;
esac
""")
        (fake_bin / "git").write_text(
            f'#!/bin/sh\nprintf "%s\\trefs/tags/%s\\n" "{commit}" "{release_tag}"\n')
        for tool in ("curl", "git"):
            (fake_bin / tool).chmod(0o755)
        return dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")

    @staticmethod
    def fake_claude(path, version):
        path.write_text(f'#!/bin/sh\necho "{version} (Claude Code)"\n')
        path.chmod(0o755)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def manifest(version, output_sha):
        return {
            "claude": version,
            "claude_linux_arm64_sha256": "a" * 64,
            "base_bun": {"archive_sha256": "b" * 64, "binary_sha256": "c" * 64},
            "output_sha256": output_sha,
            "tui_smoke": {"ran": True, "result": "pass"},
            "ci_acceptance": {"runtime": "termux-docker/bionic", "architecture": "aarch64",
                              "version_probe": "pass", "tui_smoke": "pass"},
        }

    def run_check(self, tmp, installed_version, release_tag, approved_version, same_binary):
        """`claude update --check` against a fake GitHub."""
        tmp = Path(tmp)
        target = tmp / "claude"
        digest = self.fake_claude(target, installed_version)
        env = self.fake_github(tmp, release_tag, self.manifest(
            approved_version, digest if same_binary else "d" * 64))
        proc = subprocess.run(
            ["bash", str(SELF_UPDATE), str(target), "0", "1", str(tmp / "cache")],
            text=True, capture_output=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((tmp / "cache").exists(), "--check must not create the cache")
        self.assertEqual(proc.stderr, "")
        self.assertEqual(len(proc.stdout.splitlines()), 1, proc.stdout)
        return proc.stdout

    def test_check_decides_by_binary_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.run_check(tmp, "1.2.3", "v1.2.3", "1.2.3", same_binary=True)
        self.assertEqual(out, "Claude Code 1.2.3 is up to date (v1.2.3)\n")
        # Same version, different bytes: a re-cut with a newer toolchain.
        with tempfile.TemporaryDirectory() as tmp:
            out = self.run_check(tmp, "1.2.3", "v1.2.3-r1", "1.2.3", same_binary=False)
        self.assertIn("Update available: v1.2.3-r1 is a different build of Claude Code 1.2.3", out)
        with tempfile.TemporaryDirectory() as tmp:
            out = self.run_check(tmp, "1.2.3", "v1.2.4", "1.2.4", same_binary=False)
        self.assertIn("Update available: Claude Code 1.2.3 → 1.2.4 (v1.2.4)", out)
        with tempfile.TemporaryDirectory() as tmp:
            out = self.run_check(tmp, "1.2.5", "v1.2.4", "1.2.4", same_binary=False)
        self.assertIn("1.2.5 is newer than the latest approved release 1.2.4", out)

    # A toolchain whose build is as chatty as the real one. It writes the new
    # binary and a manifest whose output hash matches, like a reproducible build.
    NOISY_BUILD = """set -e
echo "build: graph extracted (166794458 bytes)"
echo "build: native-abi: CellSegmenter ok" >&2
printf '{"payload_vaddr": 1}\\n'
if [ -n "${CLAUDE_CODE_TERMUX_PROGRESS_FD:-}" ]; then
  echo "  Building and verifying..." >&"$CLAUDE_CODE_TERMUX_PROGRESS_FD"
fi
mkdir -p dist
printf '#!/bin/sh\\necho "1.2.4 (Claude Code)"\\n' > dist/claude
chmod +x dist/claude
sha="$(sha256sum dist/claude | cut -d' ' -f1)"
cat > dist/build-manifest.json <<EOF
{"claude": "1.2.4", "claude_linux_arm64_sha256": "$(printf 'a%.0s' $(seq 64))",
 "base_bun": {"archive_sha256": "$(printf 'b%.0s' $(seq 64))",
              "binary_sha256": "$(printf 'c%.0s' $(seq 64))"},
 "output_sha256": "$sha", "tui_smoke": {"ran": true, "result": "pass"}}
EOF
echo "build: OK claude=1.2.4 -> $PWD/dist/claude"
"""

    def run_update(self, tmp, build_script):
        tmp = Path(tmp)
        target = tmp / "claude"
        self.fake_claude(target, "1.2.3")
        new_sha = self.fake_claude(tmp / "expected", "1.2.4")
        env = self.fake_github(tmp, "v1.2.4", self.manifest("1.2.4", new_sha), build_script)
        env["TMPDIR"] = str(tmp)
        proc = subprocess.run(
            ["bash", str(SELF_UPDATE), str(target), "0", "0", str(tmp / "cache")],
            text=True, capture_output=True, env=env,
        )
        log = tmp / "cache" / "claude-code-termux" / "self-update" / "update.log"
        return proc, target.read_text(), log.read_text() if log.exists() else ""

    def test_update_shows_progress_and_logs_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, target, log = self.run_update(tmp, self.NOISY_BUILD)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[0], "Updating Claude Code 1.2.3 → 1.2.4 (v1.2.4)")
        self.assertRegex(lines[1], r"^Updated Claude Code 1\.2\.3 → 1\.2\.4 in \d+ s$")
        self.assertEqual(len(lines), 2, proc.stdout)
        self.assertEqual(proc.stderr, "  Building and verifying...\n")
        self.assertIn("1.2.4", target)
        for noise in ("graph extracted", "native-abi", "payload_vaddr", "build: OK"):
            self.assertIn(noise, log)

    def test_failed_update_explains_and_keeps_the_binary(self):
        failing = "echo 'build: graph extracted'\necho 'build: native-abi: DRIFT: new member' >&2\nexit 1\n"
        with tempfile.TemporaryDirectory() as tmp:
            proc, target, log = self.run_update(tmp, failing)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "Updating Claude Code 1.2.3 → 1.2.4 (v1.2.4)\n")
        self.assertIn("claude update: the build failed; the installed Claude Code is unchanged",
                      proc.stderr)
        self.assertIn("update.log:", proc.stderr)
        self.assertIn("    build: native-abi: DRIFT: new member", proc.stderr)
        self.assertIn("1.2.3", target)
        self.assertIn("DRIFT", log)

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


def find_bun():
    """A Bionic Bun: one on PATH, this checkout's build cache, or `claude update`'s."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    candidates = [shutil.which("bun"), *sorted(ROOT.glob("work/bun-bases/bun-*/bun")),
                  *sorted(cache.glob("claude-code-termux/self-update/bun-bases/bun-*/bun"))]
    return next((str(c) for c in candidates if c and os.access(c, os.X_OK)), None)


@unittest.skipUnless(find_bun(), "needs a Bun runtime")
class UpdateNoticeTests(unittest.TestCase):
    """The startup update notice in 20-self-update.js, run in Bun on a pty.

    process.execPath is Bun itself, so Bun plays the installed claude: the
    background check runs `bun --version` (1.4.3) and hashes the Bun binary.
    """

    BUN = find_bun()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.script = assemble_runtime(self.tmp)
        self.cache = self.tmp / "cache" / "claude-code-termux" / "update-notice"
        stat = os.stat(self.BUN)
        # JavaScript's Math.round of mtimeMs.
        self.identity = f"{stat.st_size}:{math.floor(stat.st_mtime_ns / 1e6 + 0.5)}"
        self.env = dict(os.environ, XDG_CACHE_HOME=str(self.tmp / "cache"))
        self.env.pop("CLAUDE_CODE_TERMUX_UPDATE_NOTICE", None)

    def fake_release(self, tag, version, same_build):
        sha = hashlib.sha256(Path(self.BUN).read_bytes()).hexdigest() if same_build else "d" * 64
        (self.tmp / "gh").mkdir(exist_ok=True)
        self.env = SelfUpdateTests.fake_github(self, self.tmp / "gh", tag,
                                               SelfUpdateTests.manifest(version, sha)) | {
            "XDG_CACHE_HOME": str(self.tmp / "cache")}

    def write_cache(self, line, identity=None):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(f"{identity or self.identity}\n{line}\n")

    def start(self, *args, tty=True, **env):
        """Start the runtime like `claude [args]`; what it printed."""
        cmd = [self.BUN, str(self.script), *args]
        full_env = dict(self.env, **env)
        if not tty:
            proc = subprocess.run(cmd, env=full_env, text=True, capture_output=True, timeout=60)
            return proc.stdout + proc.stderr
        master, slave = pty.openpty()
        proc = subprocess.Popen(cmd, env=full_env, stdin=subprocess.DEVNULL,
                                stdout=slave, stderr=slave)
        os.close(slave)
        out = b""
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        proc.wait(timeout=60)
        os.close(master)
        return out.decode()

    def wait_for_check(self):
        """The detached check: done once its marker is gone."""
        marker = self.cache.with_name("update-notice.checking")
        deadline = time.monotonic() + 60
        while marker.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(marker.exists(), "the background check did not finish")

    def test_a_cached_update_is_shown_on_an_interactive_start(self):
        self.write_cache("Update available: v1.2.3-r1 is a different build of Claude Code 1.2.3")
        out = self.start()
        self.assertIn("Update available: v1.2.3-r1 is a different build", out)
        self.assertFalse(self.cache.with_name("update-notice.checking").exists(),
                         "a fresh result must not start another check")

    def test_scripts_and_opt_outs_get_nothing(self):
        self.write_cache("Update available: Claude Code 1.2.3 → 1.2.4 (v1.2.4)", identity="0:0")
        self.assertEqual(self.start(tty=False), "")
        for args, env in ((("-p", "hi"), {}), (("--version",), {}),
                          ((), {"CLAUDE_CODE_TERMUX_UPDATE_NOTICE": "0"}),
                          ((), {"DISABLE_UPDATES": "1"})):
            with self.subTest(args=args, env=env):
                self.assertNotIn("Update available", self.start(*args, **env))
        self.assertFalse(self.cache.with_name("update-notice.checking").exists(),
                         "no check may start for scripts or with the notice turned off")

    def test_a_replaced_binary_is_checked_again_before_anything_is_shown(self):
        self.fake_release("v1.4.4", "1.4.4", same_build=False)
        self.write_cache("Update available: an old result", identity="0:0")
        self.assertNotIn("an old result", self.start())
        self.wait_for_check()
        self.assertEqual(self.cache.read_text().splitlines(), [
            self.identity, "Update available: Claude Code 1.4.3 → 1.4.4 (v1.4.4); run: claude update"])
        self.assertIn("Update available: Claude Code 1.4.3 → 1.4.4 (v1.4.4)", self.start())

    def test_first_start_checks_and_stays_quiet_when_up_to_date(self):
        self.fake_release("v1.4.3", "1.4.3", same_build=True)
        self.assertEqual(self.start(), "")
        self.wait_for_check()
        self.assertEqual(self.cache.read_text().splitlines(),
                         [self.identity, "Claude Code 1.4.3 is up to date (v1.4.3)"])
        self.assertEqual(self.start(), "")


if __name__ == "__main__":
    unittest.main()
