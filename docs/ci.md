# CI

工作流是 [`.github/workflows/build.yml`](../.github/workflows/build.yml)，Bionic 里的步骤在
[`.github/ci/bionic-build.sh`](../.github/ci/bionic-build.sh)。

GitHub 托管的 Ubuntu runner 用的是 glibc，单有 AArch64 runner 并不等于 Android 运行时。所以验收任务跑在官方
[`termux/termux-docker`](https://github.com/termux/termux-docker) 的 AArch64 镜像里：它提供 Bionic libc、Android 的
linker、AOSP 库和 Termux 的文件系统布局。镜像按 OCI digest 固定，各个 action 按提交 SHA 固定。

## 任务

```text
resolve ── 决定 Claude 版本，判断它是否已经发布过
tests ──── python3 -m unittest discover -s tests（x64 Ubuntu，几秒）
bionic ─── ubuntu-24.04-arm + 固定的 termux-docker 镜像：
   │       升级 Termux 并安装依赖 → scripts/build.sh（下载校验、提取、ABI 检查、改图、
   │       嵌入 runtime/、嫁接、版本探针、PTY 里渲染真实 TUI）→ 在清单里写入 ci_acceptance
release ── 唯一有写权限的任务：发布工具链 Release 和 Claude Release，只附文本凭证
```

| 触发 | 做什么 |
| --- | --- |
| 推送到 `main` | 测试和完整的 Bionic 验收；工具链指纹变了就发布工具链 Release，Claude 版本还没发布过就发布 Claude Release |
| 每天 03:00 UTC | 查询官方最新版，只有还没发布过的版本才进入 Bionic 验收，通过后自动发布 |
| 手动运行 | 验收 `latest` 或指定版本，发布规则同推送 |
| PR | 只跑 `resolve` 和 `tests`，不接触 Claude 二进制 |

`tests` 任务刻意不在 termux-docker 里重跑：部分测试会造出常规的 `/bin/sh`、`/usr/bin/env` 脚本，而真实的 Termux
文件系统没有这些路径。容器只做需要 Bionic 的检查。

## 两种 Release

- **`vX.Y.Z`**：一个 Claude 版本第一次通过验收时发布，成为 Latest。发布是不可变的，被撤回的版本重发时用
  `vX.Y.Z-rN`。
- **`toolchain-vN-<指纹>`**：指纹由 [`scripts/fingerprint.sh`](../scripts/fingerprint.sh) 计算，覆盖
  `install.sh`、`versions.json`、`scripts/`、`tools/`、`runtime/` 和 `.github/` 下所有受版本控制的文件。
  没有 tag 对应当前指纹时才发布，附当次的验收清单，并且不标记为 Latest（`/releases/latest` 必须始终指向
  Claude Release）。`claude update` 据此在同一个 Claude 版本下选用最新的、验收过的工具链。

同一个指纹在任何机器上都相同（只算受版本控制的文件），本地运行 `scripts/fingerprint.sh` 得到的就是 CI 的值。

## 发布边界

构建任务只有只读权限。`dist/claude` 只存在于临时的 CI 工作区，上传的 artifact 是明确列出的 JSON 和文本报告，
不包含二进制。release 任务是唯一有 `contents: write` 的任务，它逐个检查附件，任何超过 1 MiB 的文件都会让发布
失败，避免哪天的改动悄悄把约 250 MB 的修改版 Claude 附上去。

## 覆盖不到的

termux-docker 是真实的 Bionic 用户态，但不是完整的 Android 设备：没有 ART、应用生命周期管理、Doze 和厂商 ROM
的行为。更换 Bun 底座、最低 Android API 或依赖 Android 系统服务的功能时，物理设备仍是最终参考。
