"""tools/ that read and rewrite the Bun standalone module graph."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from common import ROOT, EMBED_PRELOAD


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


if __name__ == "__main__":
    unittest.main()
