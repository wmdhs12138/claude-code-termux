# claude-code-termux

在 Termux/Android 上原生运行最新版 Claude Code：**Bionic AArch64、单 ELF 直启、可用
`claude update` 自更新**。

不需要 glibc、proot、ptrace、Node.js，也不依赖 shell launcher、外部 preload 或
`BUN_OPTIONS`。最终的 `claude` 可以由 Android linker 直接 `execve`。

> **English:** Native Claude Code for Termux/Android. This project extracts and
> adapts the official Bun standalone module graph, then grafts it onto a
> self-built Bionic AArch64 Bun runtime. The result is one directly executable,
> self-updating ELF.

[![Claude Code](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwmdhs12138%2Fclaude-code-termux%2Fmain%2Fversions.json&query=%24.claude&label=Claude%20Code&color=blue)](https://github.com/wmdhs12138/claude-code-termux/releases)
[![toolchain](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml/badge.svg)](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml)
[![Bun Bionic](https://github.com/wmdhs12138/bun/actions/workflows/bionic-aarch64.yml/badge.svg)](https://github.com/wmdhs12138/bun/actions/workflows/bionic-aarch64.yml)

## 当前状态

- Claude Code：`2.1.272`
- Bun 底座：`1.4.3-canary.1+5fce36ebb`
- 目标平台：Android 9+（API 28+）/ AArch64 / Bionic
- 实机验证：Android 16 / AArch64
- 已验证功能：登录与对话、TUI、Bash、Read、Grep、系统 `find`/`grep`、`/exit`
- 已验证更新：`claude update --check` 与完整 `claude update --force`

版本、输入哈希和实机产物哈希统一记录在 [`versions.json`](versions.json)。

## 它是怎么工作的

官方 Linux AArch64 Claude Code 是 Bun 生成的 glibc standalone，不能直接在 Termux 的
Bionic 环境运行。本项目不重新编译 Claude 源码，而是移植官方二进制中携带的 Bun 模块图：

```text
Anthropic 官方 Claude Code（Linux AArch64 / glibc）
                         │
                         │ 提取 .bun standalone 模块图
                         ▼
                Claude JavaScript 模块图
                         │
                         ├─ 关闭 bfs/ugrep shell 遮蔽
                         ├─ 回填 Bun.ant.CellSegmenter ABI
                         ├─ 内置 Android 运行默认值
                         ├─ 注入 Bionic 自更新入口
                         └─ 修正 StringPointer / bytecode 元数据
                         │
                         ▼
        自编译 Bun 1.4.3+（Android Bionic AArch64）
                         │
                         │ 模块图嫁接 + ELF PT_LOAD 修复
                         ▼
                 dist/claude（单一 ELF）
                         │
                         ▼
             Android linker 直接 execve
```

因此，“Bionic Claude Code”准确地说是：**官方 Claude 模块图 + Termux 兼容层 + 自编译
Bionic Bun 运行时**。项目不包含、也无法获取 Claude Code 的闭源源码。

底层 `.bun` 布局、模块记录、trailer 和嫁接过程见 [`docs/format.md`](docs/format.md)。

## 快速开始

### 1. 一键安装

使用 F-Droid 或 GitHub 发布的 Termux，在 AArch64 设备上执行：

```bash
pkg update
pkg install git
git clone https://github.com/wmdhs12138/claude-code-termux.git ~/claude-code-termux
cd ~/claude-code-termux
./install.sh
claude
```

安装器会检查 Termux、AArch64 和 Android API 版本，按需安装缺少的依赖，然后构建、验证、备份旧
命令并原子安装到 `~/bin/claude`。如果 `~/bin` 尚未加入 `PATH`，末尾会打印需要添加的配置。

可以指定 Claude 版本：

```bash
./install.sh 2.1.272
```

默认目标目录可用 `CLAUDE_CODE_TERMUX_INSTALL_DIR` 修改；设置
`CLAUDE_CODE_TERMUX_SKIP_DEPS=1` 可以禁止安装器自动调用 `pkg install`。

### 2. 开发者分步构建

需要检查或修改工具链时，可以分步执行：

```bash
make test
make build VERSION=latest
make install
```

`make build` 完成官方下载与校验、模块图提取、ABI 防漂移检查、兼容层注入、ELF 嫁接、闭环校验、
版本检查和真实 TUI smoke test。`make install` 只把已经验证的 `dist/claude` 原子安装，不会再次构建。
安装到 `~/bin/claude` 的是约 228 MB 的真实 Bionic ELF，不是转发脚本。

## 直接自更新

更新器已经注入最终 ELF，不需要保留仓库副本或 launcher：

```bash
claude update --check   # 只检查并报告状态，不修改文件
claude update           # 有新版时下载、重建并替换
claude update --force   # 当前已是最新版也强制重建
```

更新链路如下：

```text
claude update
      │
      ├─ 查询 downloads.claude.ai 的 latest
      ├─ 解析本仓库 main 对应的不可变 commit
      ├─ 下载该 commit 的工具链源码包
      ├─ 下载并校验官方 Claude 与固定 Bionic Bun
      ├─ 在 Termux 缓存目录构建新的候选 ELF
      ├─ 校验候选版本并再次执行版本探针
      └─ 在目标目录内原子替换当前 claude
```

缓存位于 `${XDG_CACHE_HOME:-$HOME/.cache}/claude-code-termux/self-update`。任何下载、构建
或校验失败都会保留旧 ELF。这里不会调用 Claude 官方自更新器，因为它下载的 glibc 产物会破坏
Termux 安装。

成功更新后，更新器会检查该目录中的 `claude-<版本>-toolchain-<commit>` 缓存，默认保留
最近 2 个不同 Claude 版本，每个版本保留一份；旧格式 `toolchain-<commit>` 也会通过构建清单识别。
当前更新使用的缓存始终优先保留。普通 `claude update` 在已经是最新版时也会执行
检查，`claude update --check` 则保持只读。可通过 `CLAUDE_CODE_TERMUX_CACHE_KEEP=N` 保留更多，
但低于 2 或无效的值会回退为 2。清理失败只会给出警告，不会回滚已经安装成功的新版本。
在 Termux 交互终端，大文件下载显示单行进度条（每 5% 更新一次）；CI 或重定向日志时
只输出阶段状态和错误。

Bionic Bun 底座单独保存在 `self-update/bun-bases/bun-<sha256>`，由二进制哈希寻址并在
每次构建前重新校验。不同 Claude 版本共享这一份只读底座；首次采用共享缓存时会优先迁移
旧版本目录中哈希匹配的 Bun。只有固定 Bun 哈希变化或共享文件损坏时才重新下载。
成功安装 Claude 后默认只保留当前 Bun，删除其他底座；失败时不清理，因此失败期间新旧 Bun
可以安全共存。`CLAUDE_CODE_TERMUX_BUN_CACHE_KEEP=N` 可以提高保留数量，但不能低于 1。

## 内置兼容层

### 搜索工具

官方 standalone 的原生 prelude 会把 Bash 中的 `find`/`grep` 遮蔽到内嵌 bfs/ugrep。嫁接后的
运行时没有对应 Linux prelude，继续注入这些函数会产生 `unknown option '-G'`。

[`tools/adapt_graph.py`](tools/adapt_graph.py) 按 `searchToolsOptIn()` 的行为特征定位开关，使 Claude
回退到 Termux 的系统搜索工具，不依赖每个版本都会变化的压缩函数名。

### `Bun.ant.CellSegmenter`

Claude Code 2.1.271 起，Ink 的文本布局使用私有接口 `Bun.ant.CellSegmenter`。普通 Android Bun
没有该接口，错误又会被启动界面的 Suspense 吞掉，最终表现为 TUI 白屏。

[`tools/cellsegmenter-polyfill.js`](tools/cellsegmenter-polyfill.js) 使用 `Intl.Segmenter` 和
`Bun.stringWidth` 实现所需 ABI。构建时，[`tools/embed_preload.py`](tools/embed_preload.py) 把它
直接写入 standalone 入口模块并修正所有相对指针，因此运行时没有外部 JS 依赖。

[`tools/check_native_abi.py`](tools/check_native_abi.py) 会把 Claude 实际使用的 `Bun.ant.*` 和
CellSegmenter 成员与白名单比较。接口新增、删除或改名时构建立刻失败，避免生成“能显示版本、打开
TUI 却白屏”的假成功产物。

### Android 默认值

最终 ELF 会在 Android 上内置以下默认行为，无需用户导出环境变量：

| 等效设置 | 作用 |
|---|---|
| `USE_BUILTIN_RIPGREP=0` | 使用 Termux 的 `rg`，不执行内嵌 Linux ripgrep |
| `DISABLE_AUTOUPDATER=1` | 禁用 glibc 官方更新器，由嵌入式 Bionic 更新器接管 |

账号、模型、API 端点和代理设置没有被改写，仍使用 Claude Code 官方行为。需要自定义时直接在
shell 配置中设置相应的 `ANTHROPIC_*` 环境变量。

## 构建可信度与可复现性

[`versions.json`](versions.json) 锁定以下内容：

- 官方 Claude Code 版本和 Linux AArch64 SHA-256
- Bionic Bun 的不可变 release URL、压缩包 SHA-256 和二进制 SHA-256
- 最终 ELF、嫁接模块图的 SHA-256 与文件大小
- 最后一次 Android 实机验证的平台和日期

构建在替换 `dist/claude` 前完成全部检查；固定输入的哈希不一致时会停止并保留旧产物。底座 Bun
来自 [`wmdhs12138/bun`](https://github.com/wmdhs12138/bun) 的版本化 release，不跟随会原地变化的
滚动 canary。

评估新 Bun 底座时必须显式执行：

```bash
BUN_URL=<candidate-url> make refresh-base
```

只有候选在 Android 上通过直接执行和 TUI 验证后，才应更新锁文件。

## CI、Action 与发布

本仓库的 CI 每日检查最新版 Claude，运行回归测试，并在 x64 runner 上完成模块图和 graft 结构
校验。由于 runner 不能执行 AArch64/Bionic 文件，它只发布小型文本凭证和工具链 tag，**不发布
Claude 二进制**。

Bun fork 的 [`bionic-aarch64.yml`](https://github.com/wmdhs12138/bun/actions/workflows/bionic-aarch64.yml)
使用原生 ARM64 runner 从源码构建 Bionic Bun；手动启用 `build_claude` 时，还会用指定版本和不可变
工具链 commit 生成短期 Claude Action artifact。该产物只用于拥有相应使用权的个人验证，不进入
长期 Release。

工具链 tag 格式为 `toolchain-v<N>-<fingerprint>`。指纹由 `scripts/`、`tools/` 和 `.github/`
全部已跟踪内容计算，可在本地复算：

```bash
make fingerprint
```

## 常用维护命令

```bash
make test                    # 工具链回归测试
make build VERSION=latest    # 构建并在当前 Android 设备验证
make install                 # 原子安装已有 dist/claude，并备份旧命令
make verify                  # 输出 dist/claude 版本
make smoke                   # 运行 7 秒 TUI smoke test
make fingerprint             # 计算工具链指纹
make clean                   # 删除模块图和最终 ELF
make distclean               # 删除全部下载缓存和构建产物（约 1 GB）
```

## 仓库结构

```text
Makefile                  构建、验证和维护入口
install.sh                平台检查、依赖安装、构建与原子安装
versions.json             输入与实机产物锁定信息
scripts/build.sh          完整构建事务
scripts/fetch-claude.sh   官方版本解析、下载和校验
scripts/update.sh         旧 launcher 的兼容更新入口
scripts/launcher.sh       旧安装方式的兼容 launcher
tools/extract_graph.py    提取官方 .bun standalone 图
tools/adapt_graph.py      搜索工具兼容改写
tools/embed_preload.py    把 Android 兼容层注入入口模块
tools/check_native_abi.py 私有 Bun.ant ABI 防漂移检查
tools/revive_patch.py     模块图嫁接和 ELF 修复
tools/verify_graft.py     graft 闭环验证
tools/tui_smoke.py        PTY/TUI 实机启动测试
docs/format.md            Bun standalone 格式逆向记录
evidence/                 最近一次实机构建凭证
```

## 已知限制

- 只验证了 AArch64、Android 9+；不支持 32 位 ARM、x86 或 Android 8 及以下。
- 当前模块图格式要求 Bun 1.4.3 或兼容版本；不能随意替换 Bun 底座。
- `Bun.ant.*` 是 Anthropic 私有 ABI，上游升级可能触发防漂移构建失败，需要补充兼容层。
- 最终 ELF 约 228 MB；自更新需要下载官方 Claude、Bionic Bun 和工具链，首次更新会占用较多流量与
  缓存空间。
- 内嵌 Linux ripgrep 不可用，项目固定使用 Termux 的 `ripgrep` 包。

## 法律与分发

本仓库只包含 MIT 工具链，不包含 Anthropic 的程序代码。构建时由用户设备从官方 CDN 下载 Claude
Code 并在本地处理。Claude Code 是 Anthropic 的闭源产品，请遵守其许可与服务条款。

生成的 ELF 是 Anthropic 专有程序的修改副本，**仅供个人研究与自用，请勿重新分发**。Bionic Bun
底座本身是 MIT 软件，可以单独发布；这也是 Bun release 与 Claude Action artifact 分开的原因。

## 致谢

- [Hope2333/opencode-termux](https://github.com/Hope2333/opencode-termux)：
  `BUN_COMPILED.size` 和 PT_LOAD 移植方法（MIT）
- [oven-sh/bun](https://github.com/oven-sh/bun)：Bun 与 Android Bionic 构建支持
- [Anthropic Claude Code](https://github.com/anthropics/claude-code)
- [Termux](https://github.com/termux/termux-app)

## License

MIT，仅适用于本仓库工具链。Vendored 文件继续遵循各自的原始许可证，见 [`LICENSE`](LICENSE)。
