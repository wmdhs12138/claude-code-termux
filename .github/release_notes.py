#!/usr/bin/env python3
"""Generate concise, credentials-only GitHub release notes.

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


def claude_notes(manifest, versions, toolchain):
    m, v = manifest, versions
    ver = m.get("claude", "?")
    bun = m.get("base_bun") or {}
    vo = v.get("verified_output") or {}
    device = vo.get("device") if v.get("claude") == ver else None
    verified_on = vo.get("verified_on") if v.get("claude") == ver else None
    ci_sha, dev_sha = m.get("output_sha256"), vo.get("sha256")
    acceptance = m.get("ci_acceptance") or {}
    bionic_pass = (
        acceptance.get("runtime") == "termux-docker/bionic"
        and acceptance.get("architecture") == "aarch64"
        and acceptance.get("version_probe") == "pass"
        and acceptance.get("tui_smoke") == "pass"
        and (m.get("tui_smoke") or {}).get("ran") is True
        and (m.get("tui_smoke") or {}).get("result") == "pass"
    )
    if verified_on and ci_sha and dev_sha and ci_sha == dev_sha:
        verification = f"{device} 实机验证通过（{verified_on}），与 CI 产物哈希一致。"
    elif verified_on:
        verification = "⚠️ 实机验证记录与本次 CI 产物哈希不同，请核对附件中的构建凭证。"
    elif bionic_pass:
        verification = "Bionic AArch64 直接运行、版本探针和 PTY/TUI 渲染通过。"
    else:
        verification = "⚠️ 仅完成结构校验，尚无 Bionic 运行验收。"

    return f"""# Claude Code {ver} · Termux 原生移植

验收：{verification}

已有安装运行 `claude update`；首次安装见 [README](https://github.com/wmdhs12138/claude-code-termux#安装)。
本 Release 只附构建凭证，不提供 Claude 二进制。

| 凭证 | 值 |
|---|---|
| 官方 linux-arm64 SHA-256 | `{m.get("claude_linux_arm64_sha256", v.get("claude_linux_arm64_sha256", "?"))}` |
| 本地构建产物 SHA-256 | `{ci_sha or "?"}` |
| Bun 底座 | `{bun.get("version", "?")}` |
| 工具链 | `{toolchain}` |

完整数据见附件 `build-manifest.json`；源码构建命令：`make build VERSION={ver}`。
"""


def toolchain_notes(toolchain, fingerprint, predecessor, changelog, versions_doc):
    if predecessor and predecessor != "-":
        scope = f"相对 `{predecessor}` 的变更："
    else:
        scope = "首个工具链版本的变更："
    base = (versions_doc.get("base_bun") or {}).get("version") or "?"
    return f"""# 工具链 {toolchain.split("-")[1]} (`{fingerprint}`)

用于在 Termux 构建 Bionic AArch64 Claude Code；当前固定 Bun `{base}`。
本 Release 附当次 Bionic 验收清单，不提供 Claude 二进制。

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
            a.toolchain, a.fingerprint, a.predecessor, changelog,
            load(a.versions),
        )

    with open(a.out, "w") as f:
        f.write(notes)
    print(f"wrote {a.out} ({len(notes)} bytes)")


if __name__ == "__main__":
    main()
