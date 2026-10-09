# 维护手册

给维护者（包括以后的自己）。日常发布全在 CI 里完成；手机上做的是改兼容层、跑测试和本地构建验证。

```bash
python3 -m unittest discover -s tests   # 几秒，不联网
scripts/build.sh 2.1.295                 # 官方二进制和 Bun 底座都缓存在 work/，第二次起约半分钟
```

## 上游发新版本

每天 03:00 UTC 的 CI 会发现新版本，构建、在 termux-docker 里验收通过后自动发布，不需要人工操作。构建失败时，
按失败的步骤处理（各检查的含义见 [adaptations.md](adaptations.md#构建时的检查)）：

| 失败在 | 通常意味着 | 怎么办 |
| --- | --- | --- |
| `native-abi: DRIFT` | Claude 对 `Bun.ant` 的用法变了：新成员、CellSegmenter 调用面变化，或换了写法导致看不见调用点 | 在模块图里找到新的调用点（`tools/extract_graph.py` 提取后搜索），扩展 `runtime/` 里对应的模块，再更新 `tools/check_native_abi.py` 的已知集合；`tests/test_graph.py` 里有每种情况的最小样例 |
| `searchToolsOptIn() getter not found` | 这个 getter 的写法变了 | 在模块图里找到新的写法，更新 `tools/adapt_graph.py` 的正则，加测试样例 |
| 嫁接或版本探针（段错误） | 模块图格式随 Bun 版本变了（例如 1.4.3 新增的 flag bit 11/12） | 需要更新的 Bun 底座，见下文；格式细节见 [format.md](format.md) |
| TUI 冒烟 | 能启动但画不出界面，几乎总是渲染层的私有接口 | 在手机上直接运行 `dist/.claude.new` 看报错 |

手动重跑：`gh workflow run build.yml -f version=<版本>`。

## 改运行时（`runtime/`）

- 一个关注点一个文件，`NN-名字.js`，自包含的 IIFE，按编号顺序执行；`20-self-update.js` 会直接结束进程，
  所以要在它之前生效的放在更小的编号。
- 只在 `process.platform === "android"` 时生效，不覆盖已有的原生实现和用户显式设置的环境变量。
- 需要嵌入别的文件时写 `__include__("文件名")`，构建时替换成字符串字面量（`self-update.sh` 就是这样嵌入的）。
- 构建清单的 `adaptations` 会自动列出新模块。推上去不会单独发布，见下面的「发版」。
- 测试放在 `tests/test_runtime.py`。能在真实 Bionic 上跑的就跑（参考 peer 凭据的测试找 Bun 的方式）；
  改动前先确认新测试在旧版本上会失败。

## 更换 Bun 底座

底座是 [wmdhs12138/bun](https://github.com/wmdhs12138/bun) 发布的 Bionic 构建，`versions.json` 按 URL 和
SHA-256 锁定。评估新底座必须在物理设备上进行：

```bash
REFRESH_BASE=1 BUN_URL=<候选 zip 的 URL> scripts/build.sh latest
```

候选会经历完整的构建、版本探针和 TUI 冒烟，全部通过后才把新的版本、URL 和哈希写进 `versions.json`；
任何一步失败，`versions.json` 和原有的缓存都不受影响。`REFRESH_BASE=1` 不能和 `SKIP_RUN=1` 同用。提交前
再在手机上用几天，termux-docker 不覆盖 Android 的应用生命周期、Doze 和厂商 ROM。

## 发版

只有一种 Release，和 codex-termux 一样：

- `vX.Y.Z`：一个 Claude 版本第一次通过 Bionic 验收时自动发布。tag 指向 CI 构建它的那个提交，
  `claude update` 就从这个提交构建。
- `vX.Y.Z-rN`：工具链改了（`runtime/`、`tools/`、`scripts/`……）但 Claude 版本没变时，手动重新发布：

  ```bash
  gh workflow run build.yml -f version=latest -f recut=true
  ```

  不想等可以这样做；不急的话，改动会随下一个 Claude 版本自然发布。发布是不可变的，被撤回的版本名永远被占用，
  release 任务遇到这种情况会自动改用下一个 `-rN`。
- 只有最高的 Claude 版本会成为 Latest，所以重新发布旧版本不会让所有安装回退。
- 发布说明列出相对上一个 Release 的工具链变更（不含 `docs/`、`tests/` 和 Markdown）。
- Release 只附小于 1 MiB 的文本凭证，release 任务会拒绝任何更大的文件。
- 推送会触发完整的 Bionic 验收；只改文档时在提交信息里写 `[skip ci]` 省掉这一轮。

## 兼容约束

已经装到用户手机上的 `claude update`（`runtime/self-update.sh`）和旧克隆里的 `scripts/install-approved.sh`，
会下载最新 Release 指向的提交来构建，所以下面这些对它们是接口，改了会让已安装的版本更新失败：

- 入口 `bash scripts/build.sh <版本>`，以及环境变量 `CLAUDE_CODE_TERMUX_SHARED_BUN_CACHE`；
- 产物 `dist/claude` 和 `dist/build-manifest.json`，清单字段 `claude`、`claude_linux_arm64_sha256`、
  `base_bun.archive_sha256`、`base_bun.binary_sha256`、`tui_smoke.ran`、`tui_smoke.result`；
- `bash install.sh --no-build`；
- `scripts/update.sh`：早期的 shell launcher 会调用它，现在只是一个提示迁移的存根，不能删。

2026-10 之前装的版本还会去找 `toolchain-vN-*` tag：只有当某个工具链 tag 的清单对应同一份官方二进制时才改用它，
否则用 Release 自己的 tag。现存的工具链 tag 最晚对应 2.1.294，所以从下一个 Claude 版本起，旧版本也会从 Release
的 tag 构建，并换上新的更新器。这些旧 tag 不再新增，留着无害；删掉也不影响任何版本的更新。

## 踩过的坑

- **同一份模块图换个 Bun 版本可能直接段错误。** 1.4.2 不认识 1.4.3 新增的 flag bit 11/12 及其布局，读到
  `0x40` 就崩。Bun ≥ 1.4 的 `BUN_COMPILED.size` 是不带重定位的 payload 虚拟地址（plain-offset），≤ 1.3 是要
  重定位的绝对指针，混用会在初始化前段错误（`tools/revive_patch.py` 按底座版本自动选择）。
- **字节码版本不符不会报错**，JSC 会回退到解析内嵌源码。所以改了源码的模块必须清零它的 bytecode 指针，
  否则补丁不生效。
- **2.1.271 起渲染依赖 `Bun.ant.CellSegmenter`。** 缺失时渲染器在第一帧抛错，用户看到的只是一片空白的终端，
  而版本探针照样通过，所以构建里必须有真实的 TUI 冒烟。2.1.294 把构造函数挪进一个共享 chunk、通过工厂函数导出，ABI 检查要跟着导入走。
- **Android 上 `/tmp` 不可写**，临时文件一律放 `$TMPDIR`；`/proc/pressure/*` 对应用不可读（`EACCES`）；
  `unshare(CLONE_NEWUSER)` 返回 `EINVAL`。
- `bun:ffi` 在 Bionic 上可用：`dlopen("libc.so")` 能直接打开系统 libc；在 entry 模块里用
  `process.getBuiltinModule("bun:ffi")` 取，不依赖模块是否经过转译。
- 验证跨会话消息：用两个 `claude -p` 会话互发并加 `--debug-file`，看 `[uds-client]`、`[peer-cred]` 日志。
  `--allowedTools` 是可变参数，会把后面的提示词也吃掉，提示词要放在它前面。
