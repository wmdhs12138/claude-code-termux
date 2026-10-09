# 已知限制与验证范围

运行的是官方模块图本身（2.1.295 有 2577 个模块），对话、工具、MCP、插件、技能、子代理、钩子、无头模式
都是同一份代码。官方打包时把 `process.platform` 内联成了 `"linux"`，所以虽然 Bionic Bun 实际报告
`android`，Claude 的 JS 几乎全部走 Linux 桌面的分支。差异集中在原生库和系统设施这一层。

以下结论来自 2.1.295 的模块图，以及在一台 Android 16、内核 5.15 的手机上的实测。

## 做不了（Android 平台限制）

- **沙箱。** Linux 沙箱依赖 bubblewrap、socat 和 seccomp 辅助程序，Android 上都没有（应用无法创建 user
  namespace）。命令以当前 Termux 用户的权限运行，约束只剩权限提示。
- **语音模式。** 内嵌的 `audio-capture.node` 链接 glibc 和 `libasound.so.2`，在 Bionic 上 dlopen 失败；
  Claude 的退路是调用 `arecord`（ALSA）或 SoX 的 `rec` 读取 16 kHz 单声道 PCM，Termux 默认都没有。
- **剪贴板图片粘贴。** 内嵌的 `clipboard-napi.node` 链接 glibc，加载失败；退路是 `xclip` / `wl-paste`，
  Android 上没有。终端里的文字粘贴不受影响。
- **IDE、Chrome 扩展、桌面应用联动**：依赖桌面环境，不适用。

## 与桌面版的差异

| 方面 | 差异 |
| --- | --- |
| 运行时 | 公版 Bun 1.4.3-canary 的 Bionic 构建，不是 Anthropic 私有的 bun-internal。私有的 `Bun.ant` 接口只补了 Claude 在这里会用到的三个（`CellSegmenter`、`getPeerPid`、`getPeerUid`），见 [adaptations.md](adaptations.md)。 |
| TUI 渲染 | `CellSegmenter` 是纯 JS 实现。SGR 只解析粗体、暗淡、斜体、下划线、反色、删除线和 16 / 256 / 真彩色，闪烁、隐藏、上划线、下划线颜色与样式会被忽略。 |
| 字节码 | 官方模块图带有为私有运行时生成的字节码。版本不符时 JSC 会回退到解析内嵌源码，这个 Bun 是否接受官方字节码没有单独验证。实测 `--version` 约 36 ms，`--help` 约 0.85 s。 |
| 搜索 | Bash 里的 `grep`、`find` 是 Termux 的 GNU 版本，不是内嵌的 ugrep、bfs，选项和输出格式可能略有不同；Grep / Glob 工具用 Termux 的 `ripgrep`。 |
| 低内存判断 | 走 Linux 分支的 `os.freemem()`，Bun 取的是 `MemAvailable`，结果准确。`/proc/pressure/memory` 对应用不可读。 |
| 更新 | 只跟随本项目 CI 验收过的 Release，最多晚一天，并且每次在手机上本地构建。 |

## 可能的补法（未实现）

| 功能 | 思路 | 难点 |
| --- | --- | --- |
| 语音输入 | 安装 Termux 的 `sox`，让 `rec` 经 `pulseaudio` 的 `module-sles-source` 从麦克风取音；或者包装 Termux:API 的 `termux-microphone-record` | 都没验证。需要 Termux 拿到麦克风权限；`termux-microphone-record` 只能录到文件，而 Claude 读取的是连续的 PCM 流 |
| 剪贴板图片 | 无 | Termux:API 的剪贴板只支持文字 |

## 验证过什么

在上述手机上实际运行过（2026-10-09，2.1.295）：

- 交互 TUI 启动与渲染（每次构建的冒烟测试，CI 里也在 termux-docker 中执行）。
- `claude -p` 往返、Bash、Grep、Glob、`claude update --check`。
- 跨会话消息，新旧版本对照：收件端核验发送方 pid（旧版记录 `Bun.ant.getPeerPid is not a function`，新版无告警），
  `notify_when_idle` 订阅（旧版「could not be sent」，新版「Subscribed」）。
- 两个内嵌原生插件在 Bionic 上加载失败：`libpthread.so.0`、`libasound.so.2` not found。

**没验证**：插件市场安装、远程 MCP 的 OAuth 登录、长时间后台会话在 Doze 和厂商 ROM 杀进程下的行为
（termux-docker 不覆盖 Android 的应用生命周期，物理设备是最终参考）。
