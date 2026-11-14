# claude-code-termux

English · [中文](README.md)

Run the official Claude Code natively on Termux / Android.

This project extracts Claude's module graph from the official Linux AArch64 standalone, adds a
Termux compatibility layer and grafts the result onto a Bionic Bun. The output is a single ELF
that the Android linker executes directly: no glibc, proot, Node.js, shell launcher or external
preload.

[![Claude Code](https://img.shields.io/github/v/release/wmdhs12138/claude-code-termux?display_name=tag&filter=v*&sort=semver&label=Claude%20Code&color=blue)](https://github.com/wmdhs12138/claude-code-termux/releases/latest)
[![build](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml/badge.svg)](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml)
[![Bun](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwmdhs12138%2Fclaude-code-termux%2Fmain%2Fversions.json&query=%24.base_bun.version&label=Bun&color=black)](https://github.com/oven-sh/bun/releases)

## Install

Requirements: AArch64, Android 9 (API 28) or later, and Termux from F-Droid or GitHub.

```bash
pkg update
pkg install git
git clone --depth 1 https://github.com/wmdhs12138/claude-code-termux.git ~/claude-code-termux
cd ~/claude-code-termux
./install.sh
claude
```

The installer refreshes the package index, upgrades the Termux environment and installs the
dependencies. It then picks the latest Claude release that passed the Bionic CI, builds and
verifies it in Termux's temporary directory, and installs it atomically to `$PREFIX/bin/claude`.
The final ELF is over 200 MiB, and the first build needs extra download and temporary space.
Once installed you can delete the clone; updates do not depend on it.

- A specific version: `./install.sh VERSION` (built with the sources of the current clone).
- Install location: override with `CLAUDE_CODE_TERMUX_INSTALL_DIR`.
- Dependencies already prepared: `CLAUDE_CODE_TERMUX_SKIP_DEPS=1` skips the package update. If an
  outdated mirror causes version conflicts, run `termux-change-repo` and pick one that syncs
  promptly.
- On reinstall the existing `claude` is kept as `.claude.backup` in the same directory (one copy
  only). Roll back with `mv -f "$PREFIX/bin/.claude.backup" "$PREFIX/bin/claude"`.

## Update

```bash
claude update --check   # show the latest approved version, change nothing
claude update           # build and replace when a newer approved version exists
claude update --force   # rebuild the latest approved release
```

As on the desktop, the TUI updates itself in the background. It checks at startup and every 30
minutes after that; when a newer approved version exists it builds and replaces the binary in the
background, the status line shows `✓ Update installed · Restart to update`, and the new version
runs after a restart. The build is logged to
`~/.cache/claude-code-termux/self-update/auto-update.log` and never touches the interface. A
`-rN` re-release of the same version is installed the same way, but the version number is
unchanged, so the status line says nothing. Turn auto-update off with the official
`DISABLE_AUTOUPDATER=1`.

The official install flow is never called, because it downloads a glibc build that cannot run on
Termux. `claude update` and auto-update follow only releases this project's CI has accepted: the
sources of that release are built in a temporary directory, the build is reproducible, and the
resulting binary must match the hash CI accepted before it atomically replaces the old one. If any
step fails, the existing `claude` is left untouched. Whether you are up to date is decided by the
binary hash, so a `vX.Y.Z-rN` re-release of the same Claude version with an updated toolchain is
recognized too. A version upstream has just published and CI has not yet passed does not appear;
CI checks once a day.

With auto-update off, an interactive start prints one line before the interface when a background
check has found a new approved build (a new version or an `-rN`):
`Update available: …; run: claude update`. The fullscreen interface covers it; it is visible after
you quit. The check runs at most every 20 hours and only notifies, it never installs. `-p`, pipes
and `--version` neither notify nor check. `CLAUDE_CODE_TERMUX_UPDATE_NOTICE=0` (or the official
`DISABLE_UPDATES` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`) turns the notice off as well.

The terminal shows progress only. Build details go to
`~/.cache/claude-code-termux/self-update/update.log`; on failure the reason and the last lines of
the log are shown.

## Features

| Feature | Status |
| --- | --- |
| Interactive UI, `claude -p`, Bash / Read / Edit / Write, MCP, plugins, skills, subagents, hooks | Works. This is the official module graph itself, and CI renders the real TUI on Bionic |
| Grep / Glob tools, `grep` / `find` in Bash | Work, using Termux's `ripgrep`, `grep` and `find` (the embedded ones are Linux builds) |
| Cross-session messages (`SendMessage`, idle notifications, receipts) | Work; this project supplies the peer identity check |
| `claude update` | Works; follows accepted releases only and builds on the device |
| Sandbox | **Not available**: Android has no bubblewrap, so commands run with the Termux user's permissions |
| Voice mode, pasting images from the clipboard | Not available: the embedded native addons link glibc and Bionic cannot load them |
| IDE, Chrome extension and desktop app integration | Not applicable |

Details, differences from the desktop version and what could still be added are in
[docs/limitations.md](docs/limitations.md).

## Before you use it

- **There is no sandbox.** Permission prompts are the only constraint on commands; choose your
  permission mode accordingly.
- **Auto-update trusts this repository.** A background update downloads and runs the build
  scripts of the new release. The hash comparison guards against a faulty build, not against a
  compromised repository. Set `DISABLE_AUTOUPDATER=1` if you do not want that to happen
  automatically; see
  [what an update trusts](docs/adaptations.md#更新信任的是什么).
- **Do not distribute what you build.** The generated ELF is a modified copy of Anthropic's
  proprietary program, for personal research and use only. Neither this repository nor its
  releases contain a Claude binary. Follow Anthropic's license and terms of service.
- **Private runtime interfaces may change.** Claude uses the `Bun.ant.*` interfaces of Anthropic's
  private Bun. When upstream changes them, the build fails at a check instead of producing a
  broken binary, and that version is released only once the compatibility layer has caught up.

## How it works

```text
official Claude Code linux-arm64
                │ extract the Bun module graph
                ▼
      patch the graph (search_shadow)
      embed runtime/ into the entry module
       ├─ Android defaults
       ├─ Bionic `claude update`
       ├─ Bun.ant.getPeerPid / getPeerUid
       ├─ Bun.ant.loginTty
       ├─ Bun.ant.CellSegmenter
       └─ Bun.ant.isVmContextGlobal
                │ graft
                ▼
          pinned Bionic Bun
                │
                ▼
      dist/claude · single Bionic ELF
```

Changes are located by structure and behavior, not by minified variable names that change with
every version; when a target is missing or an interface has changed, the build fails. Accounts,
models, API endpoints and proxy settings are not affected.

## Documentation

The documents are in Chinese.

| | |
| --- | --- |
| [docs/adaptations.md](docs/adaptations.md) | Every change made to the official module graph, why, and the build-time checks |
| [docs/limitations.md](docs/limitations.md) | Known limitations, differences from the desktop version, possible additions and what was verified |
| [docs/format.md](docs/format.md) | The Bun standalone `.bun` section format and the graft |
| [docs/ci.md](docs/ci.md) | The CI pipeline, toolchain versions and the release boundary |
| [docs/maintaining.md](docs/maintaining.md) | Maintainer's manual: following upstream, changing the runtime, replacing the Bun base, compatibility constraints, pitfalls |
| [tests/README.md](tests/README.md) | What each test file covers |

## Build from source

```bash
python3 -m unittest discover -s tests   # regression tests, offline
scripts/build.sh latest                  # output: dist/claude and dist/build-manifest.json
./install.sh --no-build                  # install dist/claude atomically
```

`scripts/build.sh` downloads and verifies the inputs, extracts the module graph, checks the ABI,
injects the compatibility layer, grafts the ELF, probes the version and smoke-tests the real TUI.
`dist/claude` is replaced only when all of that has passed. Downloads and intermediate files live
in `work/` and can be deleted at any time. [`versions.json`](versions.json) pins only the Bun
base; see the [maintainer's manual](docs/maintaining.md) for replacing it.

AArch64 and Android 9+ only. This repository contains only the MIT-licensed toolchain; third-party
components and acknowledgements are in [THIRD_PARTY.md](THIRD_PARTY.md). This project is not
affiliated with Anthropic.
