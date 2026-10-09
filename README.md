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

安装器会刷新软件包索引、升级 Termux 环境并安装依赖，然后选取已通过 Bionic CI 的最新 Claude
Release，在 Termux 临时目录构建、验证，并原子安装到 `$PREFIX/bin/claude`。最终 ELF 超过
200 MiB，首次构建需要额外的下载和临时空间。装好后可以删除克隆的目录，更新不依赖它。

- 指定版本：`./install.sh VERSION`（用当前克隆的源码构建）。
- 安装位置：`CLAUDE_CODE_TERMUX_INSTALL_DIR` 覆盖。
- 依赖已自行准备：`CLAUDE_CODE_TERMUX_SKIP_DEPS=1` 跳过软件包更新。软件源过旧导致版本冲突时，
  先运行 `termux-change-repo` 换一个同步及时的镜像。
- 重装时原有的 `claude` 备份为同目录下的 `.claude.backup`，只保留一份；回滚：
  `mv -f "$PREFIX/bin/.claude.backup" "$PREFIX/bin/claude"`。

## 更新

```bash
claude update --check   # 查看最新已验收版本，不修改文件
claude update           # 有已验收的新版时构建并替换
claude update --force   # 重新构建已验收的最新 Release
```

不会调用官方更新器，因为它下载的是不能在 Termux 运行的 glibc 版本。`claude update` 只采用本项目
CI 验收过的 Release：在临时目录用该 Release 对应的源码构建，构建是可复现的，得到的二进制必须和 CI
验收的那个哈希一致才原子替换；任何一步失败，原有的 `claude` 都保持不变。是否最新按二进制哈希判断，
所以同一 Claude 版本因工具链更新重新发布的 `vX.Y.Z-rN` 也能识别。上游刚发布、还没通过 CI 的版本不会出现，
CI 每天检查一次。

## 功能一览

| 功能 | 状态 |
| --- | --- |
| 交互界面、`claude -p`、Bash / Read / Edit / Write、MCP、插件、技能、子代理、钩子 | 可用。运行的是官方模块图本身，CI 在 Bionic 里真实渲染 TUI |
| Grep / Glob 工具，Bash 里的 `grep` / `find` | 可用，改用 Termux 的 `ripgrep`、`grep`、`find`（内嵌的是 Linux 版） |
| 跨会话消息（`SendMessage`、空闲通知、回执） | 可用，对端身份核验由本项目补上 |
| `claude update` | 可用，只跟随已验收的 Release，在本机构建 |
| 沙箱 | **不可用**：Android 上没有 bubblewrap，命令以 Termux 用户的权限运行 |
| 语音模式、剪贴板图片粘贴 | 不可用：内嵌的原生插件链接 glibc，Bionic 加载不了 |
| IDE、Chrome 扩展、桌面应用联动 | 不适用 |

细节、和桌面版的差异，以及还能补什么，见 [docs/limitations.md](docs/limitations.md)。

## 使用前请知道

- **没有沙箱隔离。** 命令的约束只剩权限提示，请据此选择权限模式。
- **不要分发构建产物。** 生成的 ELF 是 Anthropic 专有程序的修改副本，只供个人研究与自用；
  本仓库和 Release 都不包含 Claude 二进制，请遵守 Anthropic 的许可与服务条款。
- **私有运行时接口可能变化。** Claude 使用 Anthropic 私有 Bun 的 `Bun.ant.*` 接口，上游一变，
  构建会在检查阶段失败，而不是产出坏掉的二进制；对应版本要等兼容层跟上才会发布。

## 实现

```text
official Claude Code linux-arm64
                │ extract the Bun module graph
                ▼
      patch the graph (search_shadow)
      embed runtime/ into the entry module
       ├─ Android defaults
       ├─ Bionic `claude update`
       ├─ Bun.ant.getPeerPid / getPeerUid
       └─ Bun.ant.CellSegmenter
                │ graft
                ▼
          pinned Bionic Bun
                │
                ▼
      dist/claude · single Bionic ELF
```

改动按结构和行为特征定位，不依赖每个版本都会变的压缩变量名；找不到或接口有变化时构建直接失败。
账号、模型、API 端点和代理设置都不受影响。

## 文档

| | |
| --- | --- |
| [docs/adaptations.md](docs/adaptations.md) | 对官方模块图的每一处改动、原因，以及构建时的检查 |
| [docs/limitations.md](docs/limitations.md) | 已知限制、与桌面版的差异、可以补的办法和验证范围 |
| [docs/format.md](docs/format.md) | Bun standalone `.bun` 节格式和嫁接手术 |
| [docs/ci.md](docs/ci.md) | CI 流水线、工具链版本和发布边界 |
| [docs/maintaining.md](docs/maintaining.md) | 维护手册：跟进上游、改运行时、换 Bun 底座、兼容约束、踩过的坑 |
| [tests/README.md](tests/README.md) | 各测试文件的作用 |

## 从源码构建

```bash
python3 -m unittest discover -s tests   # 回归测试，不联网
scripts/build.sh latest                  # 产物：dist/claude 与 dist/build-manifest.json
./install.sh --no-build                  # 原子安装 dist/claude
```

`scripts/build.sh` 依次完成下载校验、模块图提取、ABI 检查、兼容层注入、ELF 嫁接、版本探针和真实
TUI 冒烟，全部通过才替换 `dist/claude`。下载和中间产物在 `work/`，可以随时删除。
[`versions.json`](versions.json) 只锁定 Bun 底座；更换底座见 [维护手册](docs/maintaining.md)。

仅支持 AArch64、Android 9+。本仓库只包含 MIT 工具链，第三方组件与致谢见
[THIRD_PARTY.md](THIRD_PARTY.md)。本项目与 Anthropic 无关联。
