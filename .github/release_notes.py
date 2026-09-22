#!/usr/bin/env python3
"""Generate GitHub release notes for claude-code-termux.

Both note types are CREDENTIALS ONLY. The project ships no binary: dist/claude
is a modified copy of Anthropic's proprietary Claude Code, so a release can
only publish what reproduces it (hashes, offsets, toolchain fingerprint), never
the artifact itself.

  release_notes.py claude    <out.md> --manifest M --versions V --toolchain T
  release_notes.py toolchain <out.md> --toolchain T --fingerprint FP
                                      --predecessor TAG|- --changelog FILE
"""
import argparse
import json


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def short(h, n=12):
    return f"{h[:n]}…" if h else "?"


def claude_notes(manifest, versions, toolchain):
    m, v = manifest, versions
    ver = m.get("claude", "?")
    bun = m.get("base_bun") or {}
    vo = v.get("verified_output") or {}
    # versions.json only carries a device claim for the version it was built at.
    device = vo.get("device") if v.get("claude") == ver else None
    verified_on = vo.get("verified_on") if v.get("claude") == ver else None

    # Only claim reproducibility when the CI build and the device build actually
    # produced the same bytes. Saying "hashes match" without checking would be
    # exactly the kind of stale claim this file exists to avoid.
    ci_sha, dev_sha = m.get("output_sha256"), vo.get("sha256")
    # Facts verify_graft.py checked about this artifact. Absent for manifests
    # built before that check existed, hence the dash instead of a fake value.
    g = m.get("graft") or {}
    acceptance = m.get("ci_acceptance") or {}
    bionic_pass = (
        acceptance.get("runtime") == "termux-docker/bionic"
        and acceptance.get("architecture") == "aarch64"
        and acceptance.get("version_probe") == "pass"
        and acceptance.get("tui_smoke") == "pass"
        and (m.get("tui_smoke") or {}).get("ran") is True
        and (m.get("tui_smoke") or {}).get("result") == "pass"
    )
    graft_cell = "—"
    if g:
        graft_cell = (f"`{g.get('modules', '?')}` 模块 · entry `{g.get('entry_point', '?')}`"
                      f" · payload @ `0x{g.get('payload_vaddr', 0):x}`")
    if verified_on and ci_sha and dev_sha and ci_sha == dev_sha:
        device_line = (
            f"维护者已在本机实机验证 **{ver}**（{verified_on}，{device}），"
            f"且本 release 的 CI 构建与其 sha256 逐字节一致（`{short(ci_sha)}`）"
            "—— 说明该版本构建可复现。"
        )
    elif verified_on:
        device_line = (
            f"⚠️ 维护者实机验证过 **{ver}**（{verified_on}，{device}），但**哈希与本 release 的 CI 构建不同**："
            f"实机 `{short(dev_sha)}` vs CI `{short(ci_sha)}`。"
            "固定输入下不应出现这种情况，请核对版本锁、工具链和构建环境。"
        )
    elif bionic_pass:
        device_line = (
            f"本 release 已在固定的 `termux-docker` **Bionic AArch64** 环境中直接执行 **{ver}**，"
            "版本探针与真实 PTY/TUI 渲染均通过。常规 Claude 版本更新不再等待维护者手机手工放行；"
            "物理 Android 仍用于 Bun 底座、最低 API 和系统生命周期相关变更。"
        )
    else:
        device_line = (
            f"⚠️ **{ver}** 尚未通过 Bionic 执行验收或维护者实机验证 —— 上表只有结构校验数据。"
        )

    if bionic_pass:
        ci_scope = (
            "上表来自 **ARM64 runner + 固定 termux-docker 镜像**中的 Bionic 验收构建："
            "产物由 Android linker 直接执行，版本探针和真实 PTY/TUI 渲染通过；同时校验了 "
            "ELF/graft 闭环、模块图指纹与产物哈希。"
        )
    else:
        ci_scope = (
            "上表来自未执行产物的结构校验构建：校验了 ELF/aarch64 结构、graft 闭环、"
            "模块图指纹与产物哈希，但没有 Bionic 运行时验收。"
        )

    return f"""# Claude Code {ver} · Termux 原生移植

本 release **只含构建凭证，不含任何二进制**。产物是 Anthropic 专有代码（Claude Code）的
修改副本，本项目不分发；请在自己的设备上构建：

```bash
git clone https://github.com/wmdhs12138/claude-code-termux.git
cd claude-code-termux && make build VERSION={ver}
sha256sum dist/claude
```

## 凭证

| 项 | 值 |
|---|---|
| Claude Code | `{ver}` |
| 官方 linux-arm64 sha256 | `{m.get("claude_linux_arm64_sha256", v.get("claude_linux_arm64_sha256", "?"))}` |
| 产物 sha256 | `{m.get("output_sha256", "?")}` |
| 产物大小 | {m.get("output_size", 0):,} B |
| 模块图 sha256 | `{m.get("graph_sha256", "?")}` |
| 底座 Bun | `{bun.get("version", "?")}` |
| Bun 下载包 sha256 | `{bun.get("archive_sha256", "?")}` |
| Bun 二进制 sha256 | `{bun.get("binary_sha256", "?")}` |
| 工具链 | `{toolchain}` |
| 适配 | {", ".join(m.get("adaptations") or []) or "—"} |
| Graft 结构自检 | {graft_cell} |

## 验证范围

{ci_scope}

{device_line}

## 复现注意

底座来自版本化的不可变镜像 release；下载包和解压后二进制均有 sha256 锁。普通 `make build`
不会追随 upstream 滚动 tag。只有维护者显式指定新版 `BUN_URL` 并执行 `make refresh-base`，
通过 Android 实机验证后才会更新底座。
"""


def toolchain_notes(toolchain, fingerprint, predecessor, changelog, released,
                    versions_doc, pending="", paths="scripts tools .github"):
    if predecessor and predecessor != "-":
        scope = f"自上一个工具链版本 `{predecessor}` 以来，触及工具链文件的提交："
    else:
        scope = "首个版本化工具链。此前工具链文件的历史提交（最近 20 条）："
    # This note is written before the Claude release of the same run exists as a
    # tag (see --pending), so the released list is the tags we can see plus that
    # version. Sorting here rather than trusting the caller's order keeps the
    # merged list readable.
    rel = [x for x in (released or "").split(",") if x]
    if pending and pending not in rel:
        rel.append(pending)
    rel.sort(key=lambda s: [int(p) for p in s.split(".")])
    versions = ", ".join(rel) or "（尚无）"
    # Rendered from the same set the fingerprint hashes, so the note cannot
    # claim coverage the hash does not have.
    paths_label = " + ".join(f"`{p}/`" for p in paths.split())
    # Read from versions.json rather than hardcoding: the pinned base moves, and
    # a hand-typed "verified base" would go stale without anything noticing.
    base = (versions_doc.get("base_bun") or {}).get("version") or "?"
    vo = versions_doc.get("verified_output") or {}
    base_note = f"（{vo.get('verified_on')} 实机验证：{vo.get('device')}）" if vo.get("verified_on") else ""
    return f"""# 工具链 {toolchain.split("-")[1]} (`{fingerprint}`)

{paths_label} 全部文件内容的 sha256 前 7 位为 `{fingerprint}`，即本版本的工具链指纹。
指纹变了就会自动切一个新 tag，因此这个编号不会和实际代码漂移。

## 能力

- **图格式**：Bun standalone new-section（Bun ≥ 1.4，plain-offset 寻址）。
  Bun ≤ 1.3 的「绝对指针 + `R_AARCH64_RELATIVE` 重定位」模式不支持。
- **适配**：`search_shadow` —— 关闭 bfs/ugrep shell 遮蔽，`find`/`grep` 回退 Termux 系统二进制。
- **产物**：单个 bionic aarch64 ELF，零 glibc / 零 ptrace / 零 proot，直接 `execve`。
- **已发布过 release 的 Claude 版本**：{versions}
- **已验证的底座**：Bun `{base}`{base_note}

格式细节见仓库 [`docs/format.md`](../blob/main/docs/format.md)。
本 release 同样**不含任何二进制**。

## 变更

{scope}

{changelog.strip() or "（无）"}
"""


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="kind", required=True)

    c = sub.add_parser("claude")
    c.add_argument("out")
    c.add_argument("--manifest", required=True)
    c.add_argument("--versions", required=True)
    c.add_argument("--toolchain", required=True)

    t = sub.add_parser("toolchain")
    t.add_argument("out")
    t.add_argument("--toolchain", required=True)
    t.add_argument("--fingerprint", required=True)
    t.add_argument("--predecessor", default="-")
    t.add_argument("--changelog", required=True)
    t.add_argument("--released", default="")
    t.add_argument("--pending", default="",
                   help="Claude version released by this same run; its tag is "
                        "created after this note, so it is not in --released yet")
    t.add_argument("--paths", default="scripts tools .github",
                   help="the file set the fingerprint covers, as named in the note")
    t.add_argument("--versions", default="", help="versions.json, for the verified-base line")

    a = ap.parse_args()
    if a.kind == "claude":
        notes = claude_notes(load(a.manifest), load(a.versions), a.toolchain)
    else:
        try:
            with open(a.changelog) as f:
                changelog = f.read()
        except OSError:
            changelog = ""
        notes = toolchain_notes(
            a.toolchain, a.fingerprint, a.predecessor, changelog, a.released,
            load(a.versions), pending=a.pending, paths=a.paths,
        )

    with open(a.out, "w") as f:
        f.write(notes)
    print(f"wrote {a.out} ({len(notes)} bytes)")


if __name__ == "__main__":
    main()
