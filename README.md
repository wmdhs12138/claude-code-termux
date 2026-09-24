# claude-code-termux

让官方 Claude Code 在 Termux / Android 上原生运行。

本项目提取官方 Linux AArch64 standalone 中的 Claude 模块图，加入 Termux 兼容层，再嫁接到
Bionic Bun。最终产物是由 Android linker 直接执行的单个 ELF，无需 glibc、proot、Node.js、
shell launcher 或外部 preload。

[![Claude Code](https://img.shields.io/github/v/release/wmdhs12138/claude-code-termux?display_name=tag&filter=v*&sort=semver&label=Claude%20Code&color=blue)](https://github.com/wmdhs12138/claude-code-termux/releases/latest)
[![build](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml/badge.svg)](https://github.com/wmdhs12138/claude-code-termux/actions/workflows/build.yml)
[![Bun Bionic](https://github.com/wmdhs12138/bun/actions/workflows/bionic-aarch64.yml/badge.svg)](https://github.com/wmdhs12138/bun/actions/workflows/bionic-aarch64.yml)

## 安装

要求：AArch64、Android 9（API 28）或更高版本，以及来自 F-Droid 或 GitHub 的 Termux。

```bash
pkg update
pkg install git
git clone --depth 1 https://github.com/wmdhs12138/claude-code-termux.git ~/claude-code-termux
cd ~/claude-code-termux
./install.sh
claude
```

安装器会补齐依赖，在本机下载、构建并验证最新版 Claude，然后原子安装到
`$PREFIX/bin/claude`。指定版本可运行 `./install.sh VERSION`；目标目录可用
`CLAUDE_CODE_TERMUX_INSTALL_DIR` 覆盖。

最终 ELF 超过 200 MiB，首次构建需要额外下载和临时空间。安装完成后可以删除克隆的项目目录，
内置更新器不依赖它。

## 更新

```bash
claude update --check   # 检查最新已验收版本，不修改文件
claude update           # 有已验收新版时构建并替换
claude update --force   # 用已验收的最新工具链重新构建
```

这里不会调用 Claude 官方更新器，因为它会下载不能在 Termux 运行的 glibc 产物。内置更新器只采用
本项目已通过 Bionic CI 的 Claude Release；同版本工具链更新也须有匹配的验收清单。它在 Termux
临时目录构建候选，核对官方输入哈希、版本和 TUI 结果后才原子替换当前命令。下载、构建或验证失败时，
原有 `claude` 保持不变。上游刚发布但尚未通过 CI 的版本不会出现在 `claude update --check` 中。

官方目前只发布完整 standalone，没有跨版本差分包；HTTP Range 只能续传中断的单次下载。因此旧
Claude 包不会加速下一版本更新。官方 Claude、工具链源码和构建中间产物都放在 `$TMPDIR`，更新
成功或失败后立即删除；缓存目录只保留一份按 SHA-256 寻址的干净 Bun 底座，只有 Bun 哈希变化时
才重新下载。可用 `CLAUDE_CODE_TERMUX_BUN_CACHE_KEEP=N` 提高 Bun 底座保留数量。

## 实现

官方 Claude Code 是 Bun standalone，但 Linux AArch64 版本使用 glibc，不能直接由 Android 的
Bionic linker 加载。本项目不拥有或重新编译 Claude 源码，只移植官方二进制中的模块图。

```text
official Claude Code linux-arm64
                │ extract Bun graph
                ▼
      apply Termux compatibility
       ├─ disable bfs/ugrep shadowing
       ├─ provide Bun.ant.CellSegmenter
       ├─ embed Android runtime defaults
       └─ embed the Bionic updater
                │ graph graft
                ▼
          pinned Bionic Bun
                │
                ▼
      dist/claude · single Bionic ELF
```

补丁按结构与 ABI 特征匹配，而不是依赖每个版本都会变化的压缩变量名。上游模块布局或私有
`Bun.ant.*` 接口发生未知变化时，构建会直接失败，不会替换已有产物。账号、模型、API 端点和代理
设置不受修改。

Bun standalone 格式和嫁接细节见 [格式文档](docs/format.md)。

## CI

GitHub Actions 使用原生 ARM64 runner 和固定 digest 的官方 `termux/termux-docker` 镜像完成自动
Bionic 验收：

- Pull Request：运行工具链与模块图回归测试；
- 推送至 `main`：构建并实际执行当前版本；
- 每天：检查官方最新版，通过全部验收后自动发布；
- 手动运行：验证 `latest` 或指定版本。

发布前会校验下载哈希、私有 Bun ABI、模块图、graft 闭环、版本输出，并通过 PTY 渲染和识别真实
Claude TUI。常规 Claude 更新不再等待维护者手机手工放行；更换 Bun 底座、最低 Android API 或
涉及 Android 系统生命周期时，仍以物理设备为最终参考。详见 [CI 文档](docs/ci.md)。

CI 内部会构建并执行 Claude，但 Action artifact 和 GitHub Release **只包含小型文本凭证，不包含
Claude 二进制**。

## 从源码构建

```bash
make test
make build VERSION=latest
make install
```

`make build` 完成下载校验、模块图提取、ABI 检查、兼容层注入、ELF 嫁接、版本探针和真实 TUI
smoke test。`make install` 原子安装已经验证的 `dist/claude`。

[`versions.json`](versions.json) 保存固定输入哈希和最后一次物理 Android 验证快照。评估新的 Bun
底座时必须显式运行：

```bash
BUN_URL=<candidate-url> make refresh-base
```

只有候选在 Android 上通过直接执行和 TUI 验证后，才应接受新的 Bun 锁定值。

## 限制与分发

- 仅支持 AArch64、Android 9+；不支持 32 位 ARM、x86 或 Android 8 及以下。
- Claude 使用 Anthropic 私有 Bun ABI，上游变化可能要求更新兼容层。
- 内嵌 Linux ripgrep 不可用，项目使用 Termux 的 `ripgrep` 包。
- termux-docker 是 Bionic 用户态，不覆盖 Doze、应用回收或厂商 ROM 行为。

本仓库只包含 MIT 工具链。Claude Code 在构建时由用户设备从 Anthropic 官方 CDN 下载，并在本地
处理。生成的 ELF 是 Anthropic 专有程序的修改副本，仅供个人研究与自用，请勿重新分发，并请遵守
Anthropic 的许可与服务条款。Bionic Bun 是独立的 MIT 软件，可单独发布。

## 致谢

- [Hope2333/opencode-termux](https://github.com/Hope2333/opencode-termux)：
  `BUN_COMPILED.size` 与 PT_LOAD 移植方法
- [oven-sh/bun](https://github.com/oven-sh/bun)：Bun 与 Android Bionic 支持
- [Anthropic Claude Code](https://github.com/anthropics/claude-code)
- [Termux](https://github.com/termux/termux-app)

## License

MIT，仅适用于本仓库工具链。详见 [LICENSE](LICENSE)。
