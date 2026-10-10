# 对官方模块图的改动

本项目不拥有、也不修改 Claude Code 的源码。构建时从官方 linux-arm64 standalone 提取 Bun 模块图（格式见
[format.md](format.md)），做下面这些改动，再嫁接到 Bionic Bun。每一处都按结构或行为特征定位，不依赖
压缩后的变量名；找不到目标或接口有变化时构建直接失败，不会替换已有的 `dist/claude`。

构建清单 `dist/build-manifest.json` 的 `adaptations` 字段记录本次实际做了哪些改动，取自各步骤的报告，不是
手写的列表。

## 图内补丁：`search_shadow`（`tools/adapt_graph.py`）

官方二进制由「原生 prelude + Bun standalone」组成。prelude 内嵌 bfs 和 ugrep，并通过原生 launch options
打开 `searchToolsOptIn()`。打开后，Claude 生成的 shell 快照会注入两个函数，把 `grep`、`find` 改成以
`exec -a ugrep|bfs "$CLAUDE_CODE_EXECPATH"` 重新调用 CLI 自身，由 prelude 按 argv0 分派：

```bash
grep () { ... ( exec -a ugrep "$_cc_bin" -G --ignore-files --hidden -I ... ) }
find () { ... ( exec -a bfs   "$_cc_bin" -S dfs ... ) }
```

Bionic 嫁接产物没有 prelude，普通 CLI 收到 `-G` 就报 `error: unknown option '-G'`。

处理：按函数体特征（`return X().host.launchOptions.searchToolsOptIn()`）找到唯一的 getter，等长替换成
`return!0`，不移动任何偏移；再把所在模块的 bytecode 和 module_info 指针清零，强制它从源码编译，补丁才会
生效（其余模块仍走字节码）。效果：`grep`、`find` 不再被遮蔽，Bash 里用的是 Termux 自带的命令。

## 嵌入的运行时（`runtime/`）

`runtime/` 里每个 `NN-名字.js` 都是一个自包含的 IIFE。`tools/assemble_runtime.py` 按文件名顺序拼接，并把
`__include__("文件")` 替换成该文件内容的字符串字面量；`tools/embed_preload.py` 再把结果插到 entry module
源码之前，修正所有受影响的偏移，并清零 entry 的字节码，让它从新源码编译。最终的 ELF 不需要
`BUN_OPTIONS=--preload` 或旁路文件，可以直接执行。

模块只在 `process.platform === "android"` 时生效（CellSegmenter 除外，它只看 `Bun.ant` 里有没有原生实现），
并且从不覆盖已有的原生实现或用户显式设置的环境变量。

| 模块 | 作用 |
| --- | --- |
| `10-android-defaults.js` | 默认 `USE_BUILTIN_RIPGREP=0`（内嵌的 ripgrep 链接 glibc，Grep 工具改用 Termux 的 `rg`）和 `DISABLE_AUTOUPDATER=1`（官方自动更新器会下载 glibc 版本，把这个 ELF 换掉；它在 TUI 里挂载时和之后每 30 分钟检查一次，这个开关连同它的检查和提示一起关掉）。 |
| `20-self-update.js` + `self-update.sh` | 在 Claude 的 CLI 看到参数之前拦截 `claude update` / `upgrade`，执行 `self-update.sh`；其他交互式启动打印更新提示。见下文。 |
| `30-peer-credentials.js` | 提供 `Bun.ant.getPeerPid(fd)` / `getPeerUid(fd)`：首次调用时用 `bun:ffi` 打开 Bionic 的 `libc.so`，以 `getsockopt(SO_PEERCRED)` 读取对端的 `struct ucred`。 |
| `40-cell-segmenter.js` | 纯 JS 实现的 `Bun.ant.CellSegmenter`。 |

### `claude update`

`self-update.sh` 是一个普通的 shell 文件，可以单独运行（`bash runtime/self-update.sh TARGET 0 1 ~/.cache`）。流程：

1. 查询本项目 `releases/latest`（`vX.Y.Z` 或 `vX.Y.Z-rN`），下载它的 `build-manifest.json`，要求其中记录了
   termux-docker Bionic 上的版本探针和 TUI 冒烟都通过。
2. 当前可执行文件的 SHA-256 等于清单里的 `output_sha256` 就是最新；否则版本更高时提示「比已验收的更新」，
   其余情况（新版本，或同一版本的 `-rN` 重新发布）都需要更新。`--check` 到此为止，不创建任何文件。
3. 下载该 tag 指向的提交的源码包，在 `$TMPDIR` 里运行 `scripts/build.sh`；Bun 底座放在
   `~/.cache/claude-code-termux/self-update/bun-bases/`，按哈希寻址，跨版本共用。
4. 构建得到的输入哈希（官方二进制、Bun 压缩包、Bun 二进制）和输出哈希都必须和获准的清单相同，版本号也要
   对上，才原子替换当前可执行文件。构建是可复现的：同一个 tag 在手机上和在 CI 里得到逐字节相同的二进制。

终端上只有一行标题、下载进度条、一行「Building and verifying...」和一行结果，例如：

```text
Updating Claude Code 2.1.295 → 2.1.296 (v2.1.296)
  Downloading Claude Code (244 MiB) [####################] 100%
  Building and verifying...
Updated Claude Code 2.1.295 → 2.1.296 in 1 min 12 s
```

其余输出（构建的每一步、缓存清理）写进 `~/.cache/claude-code-termux/self-update/update.log`，每次更新覆盖。
做法是更新器把构建的 stdout/stderr 重定向到日志，同时把终端作为 fd 3 交给它，并设置
`CLAUDE_CODE_TERMUX_PROGRESS_FD=3`；`scripts/download.sh` 把进度条画在这个 fd 上，`build.sh` 在开始构建时往上面
写一行状态。进度条会按终端宽度缩短或截断标签，保证不折行（折行之后 `\r` 无法原地重绘）。失败时更新器给出
原因、日志的最后 8 行和日志路径。

### 启动时的更新提示

官方更新器关掉之后，Claude 自己的检查和提示也没了，所以 `20-self-update.js` 在不是 `update` 的启动里补一个只提示、
不安装的版本：

- 只在 stdout 和 stderr 都是终端、没有 `-p`/`--print`、`--version`、`--help` 时生效；`CLAUDE_CODE_TERMUX_UPDATE_NOTICE=0`、
  `DISABLE_UPDATES` 或 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` 关闭。
- 结果缓存在 `~/.cache/claude-code-termux/update-notice`：第一行是检查时这个二进制的大小和修改时间，第二行是
  `claude update --check` 的输出。第一行和当前二进制对得上、第二行以 `Update available:` 开头，就在启动前把它打印到 stderr。
- 缓存超过 20 小时，或者二进制已经被换掉（第一行对不上，例如刚运行过 `claude update`），就派生一个脱离会话的
  `self-update.sh` 检查进程写新的缓存，结果在下次启动时显示，启动本身不等网络。`update-notice.checking` 防止几个
  会话同时检查。
- 检查和 `claude update --check` 是同一段代码：只用 Release 页面的跳转和附件（不占 GitHub API 配额），按二进制哈希判断。

Claude 的全屏界面用备用屏幕，启动前打印的这一行会被盖住，退出后回到主屏幕时可见；用经典渲染器时一直在界面上方。

### 跨会话消息为什么需要 peer 凭据

普通的 `SendMessage` 不需要。需要的是带「期望对端 pid」的发送：`notify_when_idle` 订阅、artifact 回复的让渡与
收回、会话改名通知、暂存回执。这类发送要先读出 socket 对端的 pid 和 uid，读不出就以
`endpoint-unverifiable` 拒发；收件端也用它给每条入站消息记录内核核验过的发送方 pid。缺少这两个接口时，
旧版本会报「the subscription could not be sent」，收件端每条消息都记录一次 `Bun.ant.getPeerPid is not a function`。

`memoryPressureLevel()` 故意不补：Claude 只在 macOS 分支调用它，这里走的 Linux 分支用 `os.freemem()`，而 Bun
在 Android 上取的就是 `MemAvailable`。

### CellSegmenter

从 2.1.271 起，Claude 的 Ink 渲染器通过 Anthropic 私有运行时的 `Bun.ant.CellSegmenter` 做字形切分和画格子。
Bionic Bun 没有这个接口，渲染器在第一帧抛错，表现为终端一片空白。`40-cell-segmenter.js` 按 Claude 实际调用的
成员面实现了它：`segment`、`paint`、`setCell` 以及 `graphemes`、`sgrKeys`、`sgrCloseKeys`、`uris` 几个池。
字宽用 `Bun.stringWidth`，字形切分用 `Intl.Segmenter`。SGR 只解析 Claude 会用到的：粗体、暗淡、斜体、
下划线、反色、删除线，以及 16 色、256 色和真彩色的前景与背景。

## 构建时的检查

| 步骤 | 拦下什么 |
| --- | --- |
| `fetch-claude.sh` | 官方二进制的 SHA-256 和大小必须与 Anthropic 发布清单一致 |
| `tools/check_native_abi.py` | `Bun.ant.*` 出现新成员，或 CellSegmenter 的调用面与 `40-cell-segmenter.js` 不一致（多了、少了、换了写法导致看不见调用点）。会跟着导入去找挪到别的 chunk 里的工厂函数 |
| `tools/adapt_graph.py` | 找不到 `searchToolsOptIn()` 的 getter，或补丁没有任何模块记录覆盖（补了也不会生效） |
| `tools/assemble_runtime.py` | `__include__` 的文件不存在或名字不是普通文件名 |
| `tools/verify_graft.py` | 不执行二进制，按运行时启动时的路径走一遍嫁接链：`.bun` 大小字段 → 长度前缀 → 尾标 → Offsets → 模块表 |
| 版本探针 | 候选必须能运行，`--version` 与官方版本一致（Bun 底座不兼容时通常在这里段错误） |
| `tools/tui_smoke.py` | 在伪终端里启动候选，必须渲染出带 Claude 标识的真实 TUI 并保持存活；只有这一步能发现 CellSegmenter 之类的渲染问题 |

任何一步失败，`dist/claude` 和清单都保持原样，候选留在 `dist/.claude.new` 供排查。
