# claude-code-termux

最新版 Claude Code 的原生 Termux 移植：**零 glibc、零 ptrace、零 proot**，单个 bionic ELF 直接 `execve`。

> **English**: Run the latest Claude Code natively on Termux/Android (bionic).
> No glibc, no ptrace, no proot — a single bionic ELF produced by grafting
> Claude's Bun standalone module graph onto an Android Bun runtime.

## 状态

[![Claude Code](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwmdhs12138%2Fclaude-code-termux%2Fmain%2Fversions.json&query=%24.claude&label=Claude%20Code&color=blue)](https://github.com/wmdhs12138/claude-code-termux/releases)
[![build](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml/badge.svg)](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml)

已在 Android 16 / aarch64 实机验证：对话往返、Bash / Read / Grep / find、TUI、`/exit` 干净退出。
产物哈希与校验和见 [Releases](https://github.com/wmdhs12138/claude-code-termux/releases)。

## 原理

官方 Claude Code 是 Bun 编译的 **glibc** 单文件程序，Termux（bionic）跑不了。本项目把官方二进制里
内嵌的 **standalone 模块图**原样取出，嫁接到官方 Android Bun 运行时上：

```
downloads.claude.ai/…/linux-arm64/claude   (glibc, Bun 1.4.3)
        │  tools/extract_graph.py   定位 .bun 节，取 [u64 len][graph]
        ▼
claude-graph.bin   (~1864 modules, ~136 MB, 含源码)
        │  tools/revive_patch.py    BUN_COMPILED.size + PT_LOAD 手术
        ▼
Pinned Android Bun (bionic ELF)  ──►  dist/claude   (单 ELF, 225 MB)
```

模块图里的字节码与底座版本不符时，运行时会自动回退到内嵌源码。格式细节见 [docs/format.md](docs/format.md)。

## 适配：关闭 bfs/ugrep shell 遮蔽

官方二进制带原生 prelude，内嵌 bfs/ugrep 并开启 `searchToolsOptIn()`，于是 Claude Code 会往 Bash
会话注入 `find`/`grep` shell 函数，把调用重定向回 CLI 二进制。嫁接产物没有 prelude，这些函数会以
`-G` 调用普通 CLI 并报 `unknown option '-G'`。

`tools/adapt_graph.py` 按 `searchToolsOptIn()` 读取行为定位该开关（不依赖每版会变化的压缩函数名），
把它改成返回 true，并让所在模块强制源码编译；`find`/`grep` 即回退 Termux 系统二进制。只影响
shell 快照生成，启动开销可忽略。

## 适配：回填 Bun.ant.CellSegmenter

从 2.1.271 起，Ink 的文本布局与绘制改用了 `@anthropic-ai/bun-internal` 的原生接口
`Bun.ant.CellSegmenter`（grapheme 切分、SGR/OSC8 解析、按 cell 包装与绘制）。官方
Android Bun 底座没有这个接口，首屏渲染在 `showSetupScreens()` 里抛错又被空 Suspense
吞掉，表现就是终端空白。

`tools/cellsegmenter-polyfill.js` 用 `Intl.Segmenter` + `Bun.stringWidth` 实现了同一套
ABI（`segment` / `paint` / `setCell` 及 `graphemes`/`sgrKeys`/`uris` 池），launcher 通过
`BUN_OPTIONS=--preload …/cellsegmenter-polyfill.js` 注入；2.1.270 不引用该接口，加载它无副作用。
注意：只有走 launcher（`claude`）才会带上 preload，直接执行 `dist/claude` 需要自己设置
`BUN_OPTIONS`。

## 快速开始

Termux（F-Droid/GitHub 版）、aarch64、Android 9+（API 28+）、`pkg install python3 unzip curl ripgrep util-linux`。

```bash
git clone https://github.com/wmdhs12138/claude-code-termux.git ~/claude-code-termux
cd ~/claude-code-termux
make build          # 下载官方二进制 + 校验 + 提取 + 嫁接 + 自检
make install        # 安装到 ~/bin/claude（同名文件会被覆盖，先自行备份）
claude              # TUI
```

更新用 `claude update`（`--check` 只检查，`--force` 强制重建）。launcher 拦截了官方自更新——那会
下载 glibc 版覆盖原生产物——改为在本地重走一遍管线。

### 账号与模型

launcher 不设置任何账号、模型或端点，全部沿用官方默认。自定义写本地覆盖文件（不进仓库）。它最先被
source，所以除了 `ANTHROPIC_*`，也能覆盖 launcher 自己的 `CLAUDE_CODE_TERMUX_ROOT` / `_BIN`：

```bash
# ~/.config/claude-code/env.sh   （可用 $CLAUDE_CODE_TERMUX_ENV 换路径）
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
export ANTHROPIC_AUTH_TOKEN="sk-..."
export ANTHROPIC_MODEL="deepseek-flash"
```

## 环境变量（launcher 已处理）

| 变量 | 作用 |
|---|---|
| `USE_BUILTIN_RIPGREP=0` | **必需**：内嵌 ripgrep 是 Linux 二进制，强制用系统 `rg` |
| `DISABLE_AUTOUPDATER=1` | **必需**：防止自更新拉 glibc 版覆盖产物 |
| `BUN_OPTIONS=--preload …` | launcher 自动加：为 2.1.271+ 注入 `Bun.ant.CellSegmenter` JS 实现 |

## 仓库结构

```
Makefile                  build / fetch / verify / smoke / install
versions.json             版本与哈希锁定，构建后自动刷新
scripts/                  fetch-claude.sh · build.sh · update.sh · launcher.sh
tools/                    extract_graph.py · adapt_graph.py · verify_graft.py
                          revive_patch.py · bunsec.py / graph.py · tui_smoke.py
                          cellsegmenter-polyfill.js（运行时注入，见上）
docs/format.md            .bun 节格式逆向笔记
evidence/                 构建与验证日志
.github/                  workflows/build.yml · release_notes.py
```

## CI 与 Release

每天定时 + 手动触发，两个 job：`build`（`contents: read`）跑全流程，并做 graft 闭环自检
（`verify_graft.py`：`.bun` size 字段 → payload → trailer → 模块表）。x64 runner 执行不了
aarch64 产物，这是唯一能自动把关的地方；实机验证仍靠 `make build` 后自己跑。
`release`（`contents: write`）只下载文本报告发 release。**"不发二进制"是结构保证**：build 没有
发布权限，release 拒绝任何 > 1 MiB 的 asset。产物是 Anthropic 专有代码的修改副本，上传即分发。

| tag | 触发 | 内容 |
|---|---|---|
| `v<claude 版本>` | 官方 `latest` 变了 | 官方校验和、产物/图 sha256、底座 Bun 哈希、复现命令 + 文本报告 |
| `toolchain-v<N>-<指纹>` | `scripts/` + `tools/` + `.github/` 内容变了 | 工具链能力、格式兼容范围、变更列表 |

指纹是 `scripts/` + `tools/` + `.github/`（含发布 note 的生成器）全部内容的 sha256 前 7 位，
直接写进 tag，所以编号不会与代码漂移；`make fingerprint` 本地可复算，应与 tag 后缀一致。
release notes 区分 CI 结构校验（产物未被执行）与实机验证（见 `versions.json`）：两者 sha256 一致时
说"可复现"；固定输入下若不一致则明确警告。需要产物请自己 `make build`。

## 版本与底座锁定

`versions.json` 同时锁定 Claude 官方 sha256、底座 Bun revision、下载包 sha256、解压后二进制
sha256 和最终产物 sha256。底座下载地址指向本仓库的**不可变版本化镜像 release**，不会再因
upstream 的滚动 `canary` tag 原地换包而让定时构建随机失败。镜像只包含 MIT 许可的 Bun Android
运行时，不包含 Claude Code。

普通 `make build` 会严格核对全部哈希，不一致时在替换现有产物前停止。评估新版 Bun 时，显式传入
upstream URL 并运行 `BUN_URL=<url> make refresh-base`；只有 Android 实机构建和验证全部成功后
才会更新锁文件。回收全部缓存（`work/` 约 1 GB）使用 `make distclean`。

## 已知限制

- Claude 模块图当前要求 Bun ≥ 1.4.3 的格式；升级底座前必须重新做实机兼容性验证。
- 内嵌 ripgrep / 自动更新不可用（launcher 已绕过）。
- 产物 225 MB，未压缩；如需可自行 UPX。
- 未在 Android 9 以下、非 aarch64 设备验证。

## 法律

源码树只含工具链，不含任何 Anthropic 代码；`make build` 从官方 CDN 下载并在本地处理。基础依赖
镜像仅包含 MIT 许可的 Bun Android 运行时。Claude Code 是 Anthropic 的闭源产品，请遵守其服务
条款；嫁接产物**仅供个人研究使用，请勿再分发**。

## 致谢

- [Hope2333/opencode-termux](https://github.com/Hope2333/opencode-termux)：`revive_patch.py` 及
  `BUN_COMPILED.size` 移植手术方案（MIT）
- [oven-sh/bun](https://github.com/oven-sh/bun)：官方 Android bionic 构建
- [Anthropic Claude Code](https://github.com/anthropics/claude-code)
- [Termux](https://github.com/termux/termux-app)

## License

MIT（仅工具链）。vendored 文件遵循其原始 MIT 许可，见 [LICENSE](LICENSE)。
