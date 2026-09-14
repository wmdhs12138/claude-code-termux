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
Android Bun canary (bionic ELF)  ──►  dist/claude   (单 ELF, 225 MB)
```

模块图里的字节码与底座版本不符时，运行时会自动回退到内嵌源码。格式细节见 [docs/format.md](docs/format.md)。

## 适配：关闭 bfs/ugrep shell 遮蔽

官方二进制带原生 prelude，内嵌 bfs/ugrep 并开启 `searchToolsOptIn()`，于是 Claude Code 会往 Bash
会话注入 `find`/`grep` shell 函数，把调用重定向回 CLI 二进制。嫁接产物没有 prelude，这些函数会以
`-G` 调用普通 CLI 并报 `unknown option '-G'`。

`tools/adapt_graph.py` 把该开关的唯一读取点 `KKn()` 改成返回 true，并让其所在模块强制源码编译，
`find`/`grep` 即回退 Termux 系统二进制。只影响 shell 快照生成，启动开销可忽略。

## 快速开始

Termux（F-Droid/GitHub 版）、aarch64、Android 9+（API 28+）、`pkg install python3 unzip curl ripgrep`。

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

launcher 不设置任何账号、模型或端点，全部沿用官方默认。需要第三方端点等自定义时写本地覆盖文件
（不进仓库，也不会被 `claude update` 覆盖）：

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

## 仓库结构

```
Makefile                  build / fetch / verify / smoke / install
versions.json             版本与哈希锁定，构建后自动刷新
scripts/                  fetch-claude.sh · build.sh · update.sh · launcher.sh
tools/                    extract_graph.py · adapt_graph.py · revive_patch.py
                          bunsec.py / graph.py · strip_bytecode.py · tui_smoke.py
docs/format.md            .bun 节格式逆向笔记
evidence/                 构建与验证日志
.github/                  workflows/build.yml · release_notes.py
```

## CI 与 Release

每天定时 + 手动触发，两个 job：`build`（`contents: read`）跑全流程做结构校验；`release`
（`contents: write`）只下载文本报告发 release。**"不发二进制"是结构保证**：build 没有发布权限，
release 拒绝任何 > 1 MiB 的 asset。产物是 Anthropic 专有代码的修改副本，上传即分发。

| tag | 触发 | 内容 |
|---|---|---|
| `v<claude 版本>` | 官方 `latest` 变了 | 官方校验和、产物/图 sha256、底座 Bun 哈希、复现命令 + 文本报告 |
| `toolchain-v<N>-<指纹>` | `scripts/` + `tools/` 内容变了 | 工具链能力、格式兼容范围、变更列表 |

指纹是 `scripts/` + `tools/` 全部内容的 sha256 前 7 位，直接写进 tag，所以编号不会与代码漂移。
release notes 区分 CI 结构校验（产物未被执行）与实机验证（见 `versions.json`）：两者 sha256 一致时
说"可复现"，不一致时提示底座 canary 漂移。需要产物请自己 `make build`。

## 版本锁定与底座漂移

`versions.json` 锁 Claude 版本、官方 sha256、底座 Bun revision、产物 sha256，每次构建自动刷新
（`verified_on` 只代表本机跑通了 `--version`；CI 构建写 `null`）。

底座是 Bun **canary** 滚动 tag。`make build` 提示底座哈希漂移时，Bun 可能改了图格式，需要重新适配
（已验证 1.4.3-canary.1+a749e0a9b）。

## 已知限制

- 底座 canary 滚动，Bun 改图格式后需跟进适配。
- 内嵌 ripgrep / 自动更新不可用（launcher 已绕过）。
- 产物 225 MB，未压缩；如需可自行 UPX。
- 未在 Android 9 以下、非 aarch64 设备验证。

## 法律

本项目只含工具链，不含任何 Anthropic 代码；`make build` 从官方 CDN 下载并在本地处理。Claude Code
是 Anthropic 的闭源产品，请遵守其服务条款；产物**仅供个人研究使用，请勿再分发**。

## 致谢

- [Hope2333/opencode-termux](https://github.com/Hope2333/opencode-termux)：`revive_patch.py` 及
  `BUN_COMPILED.size` 移植手术方案（MIT）
- [oven-sh/bun](https://github.com/oven-sh/bun)：官方 Android bionic 构建
- [Anthropic Claude Code](https://github.com/anthropics/claude-code)
- [Termux](https://github.com/termux/termux-app)

## License

MIT（仅工具链）。vendored 文件遵循其原始 MIT 许可，见 [LICENSE](LICENSE)。
