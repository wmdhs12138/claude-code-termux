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
| 运行时 | Bun 官方 1.4.3 的 Android（Bionic）构建，不是 Anthropic 私有的 bun-internal。私有的 `Bun.ant` 接口只补了 Claude 在这里会用到的三个（`CellSegmenter`、`getPeerPid`、`getPeerUid`），见 [adaptations.md](adaptations.md)。 |
| TUI 渲染 | `CellSegmenter` 是纯 JS 实现，看不出和桌面版的差别。它只解析粗体、暗淡、斜体、下划线、反色、删除线和 16 / 256 / 真彩色，但 Claude 在交给渲染器之前就把 ANSI 转成了自己的样式模型，闪烁、隐藏、上划线、下划线颜色和样式在那一步已经丢掉（桌面版同样如此）：让实现多认这些样式，Bash 输出和状态栏写到终端的样式序列也完全相同。整屏 120×40 重绘约 4.6 ms，单行变化约 0.09 ms。 |
| 字节码 | 官方模块图里的字节码不被采用，所有模块都从内嵌源码解析。官方运行时基于 WebKit `35e8970`，这里的 Bun 底座是 `0c06faa`，JavaScriptCore 版本不同；去掉全部字节码前后，首帧都是约 1.3 s，`--help` 约 0.85 s。 |
| 搜索 | Bash 里的 `grep`、`find` 是 Termux 的 GNU 版本，不是内嵌的 ugrep、bfs，选项和输出格式可能略有不同；Grep / Glob 工具用 Termux 的 `ripgrep`。 |
| 低内存判断 | 走 Linux 分支的 `os.freemem()`，Bun 取的是 `MemAvailable`，结果准确。`/proc/pressure/memory` 对应用不可读。 |
| 更新 | 和桌面版一样在 TUI 后台自动更新，但只跟随本项目 CI 验收过的 Release（最多晚一天），每次在手机上本地构建（下载约 250 MB，构建约一分钟）。同一版本的 `-rN` 会装好但状态栏不提示。 |

## 可能的补法（未实现）

| 功能 | 思路 | 难点 |
| --- | --- | --- |
| 语音输入 | 安装 Termux 的 `sox`，让 `rec` 经 `pulseaudio` 的 `module-sles-source` 从麦克风取音；或者包装 Termux:API 的 `termux-microphone-record` | 都没验证。需要 Termux 拿到麦克风权限；`termux-microphone-record` 只能录到文件，而 Claude 读取的是连续的 PCM 流 |
| 剪贴板图片 | 无 | Termux:API 的剪贴板只支持文字 |
| 字节码 | 用与官方相同 WebKit 版本的 Bun 构建底座；或在构建时用这里的 Bun 重新生成每个模块的字节码 | 前者不确定 JavaScriptCore 的兼容判断是否绑定到具体构建，可能仍不被接受；后者要改写模块记录，偏离原样运行官方模块图。能省多少首帧时间也没测出，目前不打算做 |

## 验证过什么

在上述手机上实际运行过（2026-10-09，2.1.295）：

- 交互 TUI 启动与渲染（每次构建的冒烟测试，CI 里也在 termux-docker 中执行）。
- `claude -p` 往返、Bash、Grep、Glob、`claude update --check`。
- 跨会话消息，新旧版本对照：收件端核验发送方 pid（旧版记录 `Bun.ant.getPeerPid is not a function`，新版无告警），
  `notify_when_idle` 订阅（旧版「could not be sent」，新版「Subscribed」）。
- 两个内嵌原生插件在 Bionic 上加载失败：`libpthread.so.0`、`libasound.so.2` not found。
- TUI 的 SGR 处理：在伪终端里用 `!` 的 Bash 模式和自定义状态栏输出各种样式，把实际写到终端的序列和一个多认样式的
  实验版本对照，两者相同。
- 字节码：正常版本和去掉全部字节码的版本，首帧时间和 `--help` 耗时相同。

**没验证**：插件市场安装、远程 MCP 的 OAuth 登录、长时间后台会话在 Doze 和厂商 ROM 杀进程下的行为
（termux-docker 不覆盖 Android 的应用生命周期，物理设备是最终参考）。
