# CI

工作流是 [`.github/workflows/build.yml`](../.github/workflows/build.yml)，Bionic 里的步骤在
[`.github/ci/bionic-build.sh`](../.github/ci/bionic-build.sh)。

GitHub 托管的 Ubuntu runner 用的是 glibc，单有 AArch64 runner 并不等于 Android 运行时。所以验收任务跑在官方
[`termux/termux-docker`](https://github.com/termux/termux-docker) 的 AArch64 镜像里：它提供 Bionic libc、Android 的
linker、AOSP 库和 Termux 的文件系统布局。镜像按 OCI digest 固定，各个 action 按提交 SHA 固定。

## 任务

```text
resolve ── 决定 Claude 版本和发布 tag（vX.Y.Z，手动重新发布时是 vX.Y.Z-rN），判断有没有新东西要发
tests ──── python3 -m unittest discover -s tests（x64 Ubuntu，几秒）
bionic ─── ubuntu-24.04-arm + 固定的 termux-docker 镜像：
   │       升级 Termux 并安装依赖 → scripts/build.sh（下载校验、提取、ABI 检查、改图、
   │       嵌入 runtime/、嫁接、版本探针、PTY 里渲染真实 TUI）→ 在清单里写入 ci_acceptance
release ── 唯一有写权限的任务：发布 Release，只附文本凭证
```

| 触发 | 做什么 |
| --- | --- |
| 推送到 `main` | 测试和完整的 Bionic 验收；版本已经发布过就不再发布 |
| 每天 03:00 UTC | 查询官方最新版，只有还没发布过的版本才进入 Bionic 验收，通过后自动发布 |
| 手动运行 | 验收 `latest` 或指定版本；勾选 `recut` 在工具链改动后重新发布同一版本（`vX.Y.Z-rN`） |
| PR | 只跑 `resolve` 和 `tests`，不接触 Claude 二进制 |

`tests` 任务刻意不在 termux-docker 里重跑：部分测试会造出常规的 `/bin/sh`、`/usr/bin/env` 脚本，而真实的 Termux
文件系统没有这些路径。容器只做需要 Bionic 的检查。

提交信息里带 `[skip ci]` 可以让推送不触发构建。工具链的改动不会单独发布：它们随下一个 Claude 版本一起发布，
或者用 `gh workflow run build.yml -f version=latest -f recut=true` 立即重新发布当前版本。

## Release

- tag 是 `vX.Y.Z`；同一个 Claude 版本因为工具链变化重新发布，tag 是 `vX.Y.Z-rN`（发布是不可变的，会永久占用
  tag 名，所以被撤回的版本也只能用 `-rN` 重发）。
- tag 用 `--target` 钉在 CI 构建它的那个提交上。`claude update` 从这个提交构建，构建是可复现的，得到的二进制
  必须和清单里的 `output_sha256` 一致。
- 只有最高的 Claude 版本会被标为 Latest（`claude update` 跟的就是它），重新发布旧版本不会让安装回退。
- 发布说明列出相对上一个 Release 的工具链变更。

## 发布边界

构建任务只有只读权限。`dist/claude` 只存在于临时的 CI 工作区，上传的 artifact 是明确列出的 JSON 和文本报告，
不包含二进制。release 任务是唯一有 `contents: write` 的任务，它逐个检查附件，任何超过 1 MiB 的文件都会让发布
失败，避免哪天的改动悄悄把约 250 MB 的修改版 Claude 附上去。

## 覆盖不到的

termux-docker 是真实的 Bionic 用户态，但不是完整的 Android 设备：没有 ART、应用生命周期管理、Doze 和厂商 ROM
的行为。更换 Bun 底座、最低 Android API 或依赖 Android 系统服务的功能时，物理设备仍是最终参考。
