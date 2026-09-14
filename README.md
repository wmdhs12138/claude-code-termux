# claude-termux

最新版 Claude Code 的原生 Termux 移植：**零 glibc、零 ptrace、零 proot**，单个 bionic ELF 直接 `execve`。

> **English**: Run the latest Claude Code natively on Termux/Android (bionic).
> No glibc, no ptrace, no proot — a single bionic ELF produced by grafting
> Claude's Bun standalone module graph onto an Android Bun runtime.

## 状态

| 项 | 值 |
|---|---|
| Claude Code | 2.1.270 |
| 底座 | Bun 1.4.3-canary.1+a749e0a9b（官方 Android bionic 构建） |
| 产物 | `dist/claude`，224,663,024 B，sha256 `b48d789b…` |
| 验证设备 | Android 16 / aarch64 |
| 已验证 | 对话往返、Bash / Read / Grep / find 工具、TUI、`/exit` 干净退出 |

## 原理

官方 Claude Code 是 Bun 编译的 **glibc** 单文件可执行程序，在 Termux（bionic）上无法直接运行。
本项目不碰 glibc，也不做 ptrace 拦截，而是把官方二进制里内嵌的 **standalone 模块图**原样取出来，
嫁接到官方 Android Bun 运行时上：

```
downloads.claude.ai/…/linux-arm64/claude   (glibc, Bun 1.4.3)
        │  tools/extract_graph.py   定位 .bun 节，取 [u64 len][graph]
        ▼
claude-graph.bin   (1864 modules, 136 MB, 含源码)
        │  tools/revive_patch.py    BUN_COMPILED.size + PT_LOAD 手术
        ▼
Android Bun canary (bionic ELF)  ──►  dist/claude   (单 ELF, 225 MB)
```

- 模块图里的 1.4.3 字节码与底座版本不一致时，运行时会自动回退到内嵌源码（已实测验证）。
- 格式细节见 [docs/format.md](docs/format.md)。

## 移植适配（Termux-specific adaptations）

官方原生二进制带一个**原生 prelude**，它内嵌了 bfs/ugrep 多调用程序，并开启
`launchOptions.searchToolsOptIn()`。Claude Code 因此会往 Bash 会话里注入 `find`/`grep`
shell 函数，把调用重定向回 CLI 二进制（以 `bfs`/`ugrep` 身份）。bionic 嫁接产物没有
原生 prelude，这些函数会以 `-G` 调用普通 CLI 并报 `error: unknown option '-G'`。

`tools/adapt_graph.py` 在 graft 前打补丁：把该开关的唯一读取点 `KKn()` 改成返回 true，
并让所在模块强制源码编译，从而关闭 shell 遮蔽，回退到 Termux 系统 `find`/`grep`。
该适配只影响 shell 快照生成，启动开销可忽略（~0.7s，与未适配版一致）。

## 快速开始

前置：Termux（F-Droid/GitHub 版）、aarch64 设备、Android 9+（API 28+，Bun 要求）、
`pkg install python3 unzip curl ripgrep`。

```bash
git clone https://github.com/wmdhs12138/claude-termux.git ~/claude-termux
cd ~/claude-termux
make build          # 下载官方二进制 + 校验 + 提取 + 嫁接 + 自检
make install        # 安装命令到 ~/bin/claude
claude              # TUI
```

> 如果 `~/bin/claude` 已存在（例如旧的 npm 版 2.1.112 启动器），`make install` 会覆盖它，
> 先自行备份：`mv ~/bin/claude ~/bin/claude-legacy`。

更新：

```bash
claude update          # 检查并重建到最新版
claude update --check  # 只检查（有更新时退出码 1）
claude update --force  # 强制重建
```

> `claude update` 由 launcher 拦截：官方自更新会下载 glibc 版覆盖原生产物，这里改为
> 用最新官方二进制在本地重新走一遍提取/适配/嫁接管线。等价于 `make build VERSION=latest`。

### 账号与模型

launcher **不设置任何账号、模型或端点配置**：登录、模型选择、API 端点全部沿用 Claude Code
官方默认行为（`claude` 后按提示登录即可）。

需要自定义（例如第三方 Anthropic 兼容端点）时，写本地覆盖文件即可，不会进仓库、也不会被
`claude update` 覆盖：

```bash
# ~/.config/claude-code/env.sh   （可用 $CLAUDE_TERMUX_ENV 换路径）
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
export ANTHROPIC_AUTH_TOKEN="sk-..."
export ANTHROPIC_MODEL="deepseek-flash"
```

launcher 每次启动会 source 这个文件，然后 exec Claude Code。

## 环境变量（launcher 已处理）

| 变量 | 作用 |
|---|---|
| `USE_BUILTIN_RIPGREP=0` | **必需**：内嵌 ripgrep 是 Linux 二进制，强制用系统 `rg` |
| `DISABLE_AUTOUPDATER=1` | **必需**：防止官方自更新拉 glibc 版覆盖原生产物（`claude update` 已改为本地重建） |

## 仓库结构

```
Makefile                  build / fetch / verify / smoke / install
versions.json             版本与哈希锁定（Claude、底座 Bun、产物；构建后自动刷新）
scripts/
  fetch-claude.sh         下载官方 linux-arm64 二进制 + sha256 校验
  build.sh                全流程管线 + 构建指纹（dist/build-manifest.json）
  update.sh               claude update 实现（检查/重建）
  launcher.sh             claude 启动器模板
tools/
  extract_graph.py        .bun 节 → standalone 模块图
  adapt_graph.py          Termux 适配补丁（关闭 bfs/ugrep shell 遮蔽）
  revive_patch.py         移植手术（vendored, MIT, 来自 Hope2333/opencode-termux）
  bunsec.py / graph.py    格式分析辅助
  strip_bytecode.py       字节码剥离（备用方案）
  tui_smoke.py            PTY TUI 冒烟测试
docs/format.md            .bun 节格式逆向笔记
evidence/                 构建与验证日志
.github/workflows/        CI：构建 + 结构校验（不发布产物）
```

## CI

`.github/workflows/build.yml` 每天定时 + 手动触发：在 x64 runner 上跑完整管线并做
ELF/aarch64 结构校验（`SKIP_RUN=1`，runner 跑不了 bionic 产物），只上传构建指纹和日志。

**CI 不构建 Release、不上传二进制**：产物是 Anthropic 专有代码的修改副本，上传即分发。
需要产物请在自己的设备上 `make build`。

## 版本锁定与底座漂移

`versions.json` 锁定 Claude 版本、官方二进制 sha256、底座 Bun revision 与产物 sha256。
每次 `make build` / `claude update` 结束时会**自动刷新**它，不会落后于 `dist/build-manifest.json`。

- `claude_linux_arm64_sha256`：官方 linux-arm64 二进制的 sha256（`fetch-claude.sh` 下载时已比对过官方 manifest）。
- `verified_output.verified_on`：**本机执行过 `dist/claude --version` 且版本串匹配**的日期，
  即 build.sh 第 5 步的校验。TUI、Bash/Read/Grep 工具、对话往返等更深的验证仍需手工做，
  不会被自动写入。
- CI 以 `SKIP_RUN=1` 构建时产物从未被执行，`device` / `verified_on` 写 `null`，
  而不是沿用上一次的值。CI 只有 `contents: read`，不会污染已提交的记录。

底座用的是 Bun **canary** 滚动 tag，若 `make build` 提示底座哈希漂移，说明 Bun 可能改了图格式，
需要重新适配（本项目已验证 1.4.3-canary.1+a749e0a9b）。

## 已知限制

- 底座 canary 滚动，Bun 修改 graph 格式后需跟进适配。
- 内嵌 ripgrep / 自动更新不可用（launcher 已用系统 `rg` 和禁用更新绕过）。
- 产物 225 MB，未压缩；如需可自行 UPX。
- 未在 Android 8/9 以下、非 aarch64 设备验证。

## 法律

本项目只包含工具链，不含任何 Anthropic 代码。`make build` 从 Anthropic 官方 CDN 下载二进制并在本地
处理。Claude Code 是 Anthropic 的闭源产品，请遵守其服务条款；产物**仅供个人研究使用，请勿再分发**。

## 致谢

- [Hope2333/opencode-termux](https://github.com/Hope2333/opencode-termux)：`revive_patch.py` 及
  `BUN_COMPILED.size` 移植手术方案（MIT）
- [oven-sh/bun](https://github.com/oven-sh/bun)：官方 Android bionic 构建
- [Anthropic Claude Code](https://github.com/anthropics/claude-code)
- [Termux](https://github.com/termux/termux-app)

## License

MIT（仅工具链）。vendored 文件遵循其原始 MIT 许可，见 [LICENSE](LICENSE)。
