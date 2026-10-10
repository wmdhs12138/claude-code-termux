# tests/

```bash
python3 -m unittest discover -s tests -v
```

只需 `bash`、`python3` 和 coreutils，不联网、不下载 Claude，CI 的 `tests` 任务（x64 Ubuntu）和手机上都能跑。有 `node` 或 `bun` 时会额外检查 `runtime/` 的 JS 语法；能找到 Bionic Bun 时（`PATH` 上的 `bun`、本仓库 `work/bun-bases/`，或 `claude update` 的缓存）会在真实 Bionic 运行时上执行 peer 凭据测试，否则跳过。

需要真实 Bionic 和 Claude 二进制的验收（版本探针、PTY 里渲染真实 TUI）不在这里，由 `scripts/build.sh` 对每个候选执行，CI 在 termux-docker 里跑，见 [../docs/ci.md](../docs/ci.md)。

| 文件 | 作用 |
| --- | --- |
| `common.py` | 各测试共用的路径常量。 |
| `test_build.py` | `scripts/build.sh` 及其调用的脚本：`fetch-claude.sh` 拒绝非法版本、共享构建锁；所有下载都经过 `download.sh`，没有终端时静默，有终端时（或调用方给了 `CLAUDE_CODE_TERMUX_PROGRESS_FD`）在伪终端里画出带标签的单行进度条；`ensure-bun-base.sh` 按哈希缓存 Bun 底座、迁移旧布局；`versions.json` 只锁定 Bun 底座、缺字段即拒绝；`REFRESH_BASE` 不能和 `SKIP_RUN` 同用；只有接受新底座时才写 `versions.json`；提升时先删旧清单；TUI 冒烟在候选上、在提升前、不带外部 preload 运行；`tools/revive_patch.py` 嫁接时把底座落在 `.bss` 范围里的非加载内容搬走并清零、节头表仍可用；`tools/tui_smoke.py` 本身的判定（真实 Claude 标记、提前退出、通用欢迎屏都要分得清）。 |
| `test_graph.py` | 读写 Bun 模块图的工具：`adapt_graph.py` 按行为而非压缩后的名字匹配 `searchToolsOptIn()`，按日志找到 `installLatest()` 并跨过字符串、模板字面量和嵌套块确定函数边界，等长替换成钩子并清零所在模块的字节码；`check_native_abi.py` 跟踪 CellSegmenter 工厂跨 chunk 的导入、对新成员 / 缺成员 / 换写法一律失败；`embed_preload.py` 插入 entry 并修正所有偏移。 |
| `test_runtime.py` | `runtime/`：各模块和拼装结果的语法、按文件名顺序拼装且更新器先于 shim、`__include__` 缺文件即失败、Android 默认环境变量、peer 凭据不覆盖原生实现并能读到另一个进程的 pid/uid；`self-update.sh` 的语法、只认 CI 验收过的 Release、按二进制哈希判断是否最新（`-rN` 重发也算更新）、`--check` 只输出一行且只读；在假的 GitHub 和一个输出很吵的假工具链上实际跑完整更新：终端只有标题、状态行和结果，细节都在日志里，失败时给出原因和日志末尾且不动原二进制；临时目录和缓存清理；自动更新钩子 `__claudeTermuxInstallLatest`（用一份 Bun 的副本扮演正在运行的 claude，对假的 GitHub）：已是最新不动，有新版时真的构建并原子替换且终端上没有任何输出，别的会话持锁时让出，离线时什么也不做；关掉自动更新时的启动提示（在 Bionic Bun 上用伪终端运行组装好的 runtime）：自动更新开着时不提示，缓存里的更新只在交互式启动时显示，`-p`、管道、`--version` 和关闭开关什么都不做，二进制换过后不显示旧结果而是在后台对假的 GitHub 重新检查。 |
| `test_install.py` | `install.sh`：版本号非法时在其他检查之前拒绝、每次刷新并升级 Termux 依赖、依赖失败给出换源提示、`rg` 不可执行时报告、默认装到 `$PREFIX/bin`；`scripts/install-approved.sh` 走已验收的 Release 和临时目录构建、拒绝未验收的清单；原子安装并只保留一份备份；`scripts/update.sh` 这个退役存根只提示迁移、不联网不构建。 |
| `test_ci.py` | `.github/`：在临时 git 仓库里实际运行 workflow 的 resolve 和发布步骤（假的 `gh`）：新版本用 `vX.Y.Z`、重新发布取下一个 `-rN`、tag 钉在构建的提交上、旧版本重发不抢 Latest、被占用的 tag 名自动顺延、发布说明列出工具链变更；Bionic 任务用固定镜像、真实执行候选并要求 TUI 通过、上传和发布都不含二进制。 |
