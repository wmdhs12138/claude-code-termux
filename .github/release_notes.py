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
    # produced the same bytes. A canary Bun roll makes them differ, and saying
    # "hashes match" without checking would be exactly the kind of stale claim
    # this file exists to avoid.
    ci_sha, dev_sha = m.get("output_sha256"), vo.get("sha256")
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
            "这几乎总是底座 Bun canary 已漂移（见下方「复现注意」），而不是移植本身的问题。"
        )
    else:
        device_line = (
            f"⚠️ 维护者尚未在 **{ver}** 上做实机验证 —— 上表只有 CI 的结构校验数据。"
        )

    return f"""# Claude Code {ver} · Termux 原生移植

本 release **只含构建凭证，不含任何二进制**。产物是 Anthropic 专有代码（Claude Code）的
修改副本，本项目不分发；请在自己的设备上构建：

```bash
git clone https://github.com/wmdhs12138/claude-code-termux.git
cd claude-code-termux && make build
sha256sum dist/claude
```

## 凭证

| 项 | 值 |
|---|---|
| Claude Code | `{ver}` |
| 官方 linux-arm64 sha256 | `{v.get("claude_linux_arm64_sha256", "?")}` |
| 产物 sha256 | `{m.get("output_sha256", "?")}` |
| 产物大小 | {m.get("output_size", 0):,} B |
| 模块图 sha256 | `{m.get("graph_sha256", "?")}` |
| 底座 Bun | `{bun.get("version", "?")}`（`{short(bun.get("binary_sha256"))}`） |
| 工具链 | `{toolchain}` |
| 适配 | {", ".join(m.get("adaptations") or []) or "—"} |

## 验证范围

上表来自 **x64 runner 上的结构校验构建**（`SKIP_RUN=1`）：校验了 ELF/aarch64 结构、
模块图指纹与产物哈希，但**没有执行过该产物**（runner 跑不了 bionic aarch64）。

{device_line}

## 复现注意

底座是 Bun **canary** 滚动 tag。若上表钉的 Bun 哈希已经漂移，`make build` 会在结尾打
WARNING，此时复现出的产物 sha256 可能与本 release 不同 —— 那是底座换了，不是构建不可复现。
"""


def toolchain_notes(toolchain, fingerprint, predecessor, changelog, released):
    if predecessor and predecessor != "-":
        scope = f"自上一个工具链版本 `{predecessor}` 以来，触及 `scripts/` 或 `tools/` 的提交："
    else:
        scope = "首个版本化工具链。此前 `scripts/` 与 `tools/` 的历史提交（最近 20 条）："
    versions = released or "（尚无）"
    return f"""# 工具链 {toolchain.split("-")[1]} (`{fingerprint}`)

`scripts/` + `tools/` 全部文件内容的 sha256 前 7 位为 `{fingerprint}`，即本版本的工具链指纹。
指纹变了就会自动切一个新 tag，因此这个编号不会和实际代码漂移。

## 能力

- **图格式**：Bun standalone new-section（Bun ≥ 1.4，plain-offset 寻址）。
  Bun ≤ 1.3 的「绝对指针 + `R_AARCH64_RELATIVE` 重定位」模式不支持。
- **适配**：`search_shadow` —— 关闭 bfs/ugrep shell 遮蔽，`find`/`grep` 回退 Termux 系统二进制。
- **产物**：单个 bionic aarch64 ELF，零 glibc / 零 ptrace / 零 proot，直接 `execve`。
- **已发布过 release 的 Claude 版本**：{versions}
- **已验证的底座**：Bun `1.4.3-canary.1+a749e0a9b`

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
            a.toolchain, a.fingerprint, a.predecessor, changelog, a.released
        )

    with open(a.out, "w") as f:
        f.write(notes)
    print(f"wrote {a.out} ({len(notes)} bytes)")


if __name__ == "__main__":
    main()
