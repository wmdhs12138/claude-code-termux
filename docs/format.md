# Bun standalone `.bun` 节格式笔记

本文记录 claude-termux 移植过程中逆向出的 Bun standalone 模块图格式（Bun ≥ 1.4 的
"new section" 格式），以及嫁接手术的依据。所有结论都经过二进制实测；证据见
`evidence/`。

## 1. 定位

官方 Claude Code 的 ELF 里有一个 `.bun` PROGBITS 节（Claude 2.1.270: 135,681,512 B）。
新格式下：

```
.bun section = [u64 payload_len][payload ...]
```

`payload_len` 即 `section_size - 8`（实测 Claude 2.1.270 = 0x081655e0）。
运行时并不通过 ELF 节表找它，而是：

1. 符号 `BUN_COMPILED.size` 保存 payload 的**链接期虚拟地址**（unbiased vaddr）；
2. Bun ≥ 1.4（plain-offset 模式）：运行时用 `dl_iterate_phdr()` 找到覆盖该地址的 PT_LOAD，
   计算 `abs = dlpi_addr + vaddr`，再从 `abs` 读 `[u64 payload_len]`；
3. Bun ≤ 1.3.x：把该字段当绝对指针解引用，因此 PIE 底座需要追加
   `R_AARCH64_RELATIVE` 重定位（opencode-termux 的 reloc 模式）。

## 2. payload 布局（Bun ≥ 1.4，`StandaloneModuleGraph.rs`）

按写入顺序：

```
[string / bytecode / sourcemap 等数据区]
[module table]                 N × 52 B
[source hashes]                N × u32        (flag HAS_SOURCE_HASHES)
[builtin bytecode table]       u32 count + count × {u32 id, StringPointer bytes}
[bytecode string table ptr]    StringPointer  (flag HAS_BYTECODE_STRING_TABLE)
[startup module count]         u32            (flag HAS_STARTUP_MODULE_COUNT)
[module info string table ptr] StringPointer  (flag HAS_MODULE_INFO_STRING_TABLE)
[compile_exec_argv]            字符串
[Offsets]                      32 B
[TRAILER]                      "\n---- Bun! ----\n" (17 B)
```

`StringPointer = u32 offset + u32 length`，offset 相对 **payload 起点**（即节内 +8）。

### 模块记录 `CompiledModuleGraphFile`（52 B）

| 偏移 | 字段 | 说明 |
|---|---|---|
| 0 | `name` | 模块路径，如 `/$bunfs/root/chunk-xxxx.js` |
| 8 | `contents` | 源码（含 `// @bun` 前缀 / 内嵌资源） |
| 16 | `sourcemap` | source map（常为空） |
| 24 | `bytecode` | JSC 字节码 blob |
| 32 | `module_info` | 字节码模块信息 |
| 40 | `bytecode_origin_path` | 生成字节码时的路径（缓存命中用） |
| 48 | `encoding` `loader` `module_format` `side` | 4 × u8 |

### `Offsets`（32 B，在 TRAILER 之前）

```
u64 byte_count;              // payload 去掉 Offsets+TRAILER 的长度
StringPointer modules_ptr;   // module table
u32 entry_point_id;          // 入口模块下标
StringPointer compile_exec_argv_ptr;
u32 flags;
```

### Flags

| bit | 名称 |
|---|---|
| 0–3 | DISABLE_DEFAULT_ENV_FILES / AUTOLOAD_BUNFIG / AUTOLOAD_TSCONFIG / AUTOLOAD_PACKAGE_JSON |
| 4 | SOURCE_TEXT_CONTIGUOUS |
| 5 | HAS_SOURCE_HASHES |
| 6 | HAS_BUILTIN_BYTECODE |
| 7 | HAS_BYTECODE_STRING_TABLE |
| 8 | HAS_STARTUP_MODULE_COUNT |
| 9 | HAS_MODULE_INFO_STRING_TABLE |
| 10 | CROSS_COMPILED_BYTECODE |
| 11 | **HAS_PRELINKED_MODULE_GRAPH**（1.4.3 新增） |
| 12 | **HAS_RUNTIME_OPTIONS**（1.4.3 新增） |

Claude 2.1.270：`flags = 0x1fff`（bit 0–12 全开），1864 个模块，`entry_point_id = 5`
（这三项与 2.1.269 完全一致，仅 payload 体积增长）。

## 3. 实测结论

- **字节码可回退**：把模块字节码头部的版本字段改成 `0xdeadbeef` 后程序照常运行，
  说明字节码版本不符时 JSC 会回退解析内嵌源码。
- **图格式随运行时版本演进**：同一份 Claude graph 嫁接到 Bun 1.4.2 底座会
  段错误（`0x40`），因为 1.4.2 不认识 bit 11/12 及其布局；换 1.4.3-canary 底座后完全正常。
- **TUI 无关**：Claude Code 的 TUI 是 Ink/React 纯 JS，不需要像 opencode 那样自建
  `libopentui.so`；唯一的原生依赖是内嵌 ripgrep（Linux ELF），用
  `USE_BUILTIN_RIPGREP=0` + Termux `ripgrep` 替代。

## 4. 嫁接手术（Bun ≥ 1.4，plain-offset）

`tools/revive_patch.py`（vendored）：

1. 定位 `.bun` 节（要求 `addr == offset`，identity mapping）；
2. 找到覆盖它的可写 PT_LOAD；新数据放到 `max(file_end, bss_end)` 之后并按 16 KiB 对齐
   （避免覆盖 `.bss` 的零初始化区）；
3. 追加 `[u64 payload_len][payload]`；
4. 把 payload vaddr 写进 `.bun[0]`（`BUN_COMPILED.size`）；
5. 扩展该 PT_LOAD 的 `p_filesz`/`p_memsz` 覆盖新数据。

## 5. 移植适配：关闭 bfs/ugrep shell 遮蔽

官方二进制由「原生 prelude + Bun standalone」组成。prelude 内嵌 bfs/ugrep，并通过
原生 launch options 打开 `searchToolsOptIn()`。JS 侧逻辑：

```js
function KKn(){ return n().host.launchOptions.searchToolsOptIn() }   // 唯一调用点
function Qb(){ if(!Ie("true")) return !1; if(KKn()) return !1; return a.CLAUDE_CODE_ENTRYPOINT!=="local-agent" }
```

`Qb()` 为 true 时，shell 快照生成器 `yis()` 会注入：

```bash
grep () { ... ( exec -a ugrep "$_cc_bin" -G --ignore-files --hidden -I ... ) }
find () { ... ( exec -a bfs   "$_cc_bin" -S dfs ... ) }
```

其中 `_cc_bin="${CLAUDE_CODE_EXECPATH}"`（CLI 自身路径）。官方 prelude 检测 argv0
为 `ugrep`/`bfs` 时分派到内嵌程序；bionic 嫁接产物没有 prelude，于是普通 CLI 收到
`-G` 并报 `error: unknown option '-G'`。

`tools/adapt_graph.py` 的处理（等长替换，不移动任何 StringPointer）：

1. 把 `function KKn(){return n().host.launchOptions.searchToolsOptIn()}` 替换为
   `function KKn(){return!0<pad>}`；
2. 把 `KKn` 所在模块（2.1.270: `chunk-74sfngb9.js`，约 100 KB；此文件名随版本变化，
   `adapt_graph.py` 按函数体特征串定位，不写死模块名）的 `bytecode`/`module_info`
   StringPointer 清零，强制该模块从源码编译，使补丁生效（其余模块仍走字节码）。

效果：`grep`/`find` 不再被遮蔽，Bash 使用 Termux 系统二进制；Grep 工具继续走
`USE_BUILTIN_RIPGREP=0` + 系统 `rg`。启动耗时与未适配版一致（~0.7s）。

## 6. 证据

- `evidence/sha256.txt`：官方二进制校验
- `evidence/revive-1.log`：1.4.2 底座嫁接（后续段错误，记录失败路径）
- `evidence/revive-3.log`：1.4.3-canary 底座嫁接（成功）
- `dist/build-manifest.json`：每次构建的版本/哈希指纹与适配列表
