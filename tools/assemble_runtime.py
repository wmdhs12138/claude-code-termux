#!/usr/bin/env python3
"""Assemble runtime/ into the one script embedded into Claude's entry module.

Every runtime/NN-name.js is a self-contained IIFE; they are concatenated in
file-name order, so the number decides what runs first (the updater exits
before the rest is installed). `__include__("file")` is replaced with the JSON
string literal of runtime/<file>, which keeps the shell updater a real .sh
file that can be syntax-checked and run on its own.

Usage: assemble_runtime.py <runtime-dir> <out.js> [--report report.json]

The report names the modules that went in, so the build manifest can list
them from the run itself instead of keeping a hardcoded list in sync.
"""
import argparse
import json
import re
from pathlib import Path

MODULE = re.compile(r"(\d+)-([a-z0-9-]+)\.js")
INCLUDE = re.compile(r'__include__\(\s*"([^"]*)"\s*\)')
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def fail(message):
    raise SystemExit(f"assemble-runtime: {message}")


def assemble(runtime):
    modules = sorted(p for p in runtime.iterdir() if MODULE.fullmatch(p.name))
    if not modules:
        fail(f"no NN-name.js modules in {runtime}")
    includes = []

    def inline(match):
        name = match.group(1)
        if not SAFE_NAME.fullmatch(name):
            fail(f"include name {name!r} must be a plain file name inside {runtime}")
        path = runtime / name
        if not path.is_file():
            fail(f"include {name!r} does not exist in {runtime}")
        includes.append(name)
        return json.dumps(path.read_text())

    parts = []
    for path in modules:
        source = INCLUDE.sub(inline, path.read_text())
        if "__include__" in source:
            fail(f"{path.name} has an __include__ that is not a plain string literal")
        # A module ending in a // comment or without a semicolon must not run
        # into the next one.
        parts.append(f"// runtime/{path.name}\n{source.rstrip()}\n;\n")
    return "".join(parts), [p.name for p in modules], includes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime")
    parser.add_argument("output")
    parser.add_argument("--report")
    args = parser.parse_args()

    script, modules, includes = assemble(Path(args.runtime))
    Path(args.output).write_text(script)
    report = {
        "modules": modules,
        "adaptations": [MODULE.fullmatch(m).group(2).replace("-", "_") for m in modules],
        "includes": includes,
        "bytes": len(script.encode()),
    }
    if args.report:
        with open(args.report, "w") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
