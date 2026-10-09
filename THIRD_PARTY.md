# Third-party software

This repository contains build scripts, graph tools and a JavaScript runtime layer under the MIT
License (see [LICENSE](LICENSE)). It contains no Anthropic code and publishes no Claude binary.

| Component | License | Notes |
| --- | --- | --- |
| [Claude Code](https://github.com/anthropics/claude-code) | Proprietary (Anthropic) | Downloaded by `scripts/build.sh` from Anthropic's CDN on the user's device, checksum-verified against Anthropic's release manifest and modified locally. The resulting ELF is a modified copy of a proprietary program for personal use only and must not be redistributed. CI builds it in a read-only job and releases carry text credentials only. |
| [Bun](https://github.com/oven-sh/bun) | MIT | The Bionic AArch64 build published by [wmdhs12138/bun](https://github.com/wmdhs12138/bun), pinned by SHA-256 in `versions.json` and downloaded at build time. |
| `tools/revive_patch.py` | MIT | Vendored unmodified from [Hope2333/opencode-termux](https://github.com/Hope2333/opencode-termux) (native-android branch), Copyright (c) Hope2333 (幽零小喵). |
| [ripgrep](https://github.com/BurntSushi/ripgrep) and other Termux packages | various | Installed from Termux repositories and used at build or run time; not redistributed. |

"Claude" and "Anthropic" are trademarks of their owners. This project is not affiliated with or
endorsed by Anthropic.

## Acknowledgements

- [Hope2333/opencode-termux](https://github.com/Hope2333/opencode-termux): the `BUN_COMPILED.size`
  and PT_LOAD grafting method.
- [oven-sh/bun](https://github.com/oven-sh/bun): Bun and its Android Bionic support.
- [Termux](https://github.com/termux/termux-app) and [termux-docker](https://github.com/termux/termux-docker).
