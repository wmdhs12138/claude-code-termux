#!/usr/bin/env python3
"""Generate concise, credentials-only GitHub release notes.

  release_notes.py <out.md> --manifest M --tag vX.Y.Z[-rN] --commit SHA
                            --predecessor TAG|- --changelog FILE
"""
import argparse
import json
import re


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def release_notes(manifest, tag, commit, predecessor, changelog):
    m = manifest
    ver = m.get("claude", "?")
    bun = m.get("base_bun") or {}
    acceptance = m.get("ci_acceptance") or {}
    bionic_pass = (
        acceptance.get("runtime") == "termux-docker/bionic"
        and acceptance.get("architecture") == "aarch64"
        and acceptance.get("version_probe") == "pass"
        and acceptance.get("tui_smoke") == "pass"
        and (m.get("tui_smoke") or {}).get("ran") is True
        and (m.get("tui_smoke") or {}).get("result") == "pass"
    )
    if bionic_pass:
        verification = "Bionic AArch64 直接运行、版本探针和 PTY/TUI 渲染通过。"
    else:
        verification = "⚠️ 仅完成结构校验，尚无 Bionic 运行验收。"

    recut = re.search(r"-r(\d+)$", tag)
    if recut:
        what = (f"\n这是 {ver} 的第 {recut.group(1)} 次重新发布：Claude 版本不变，构建工具链有更新。"
                "`claude update` 按二进制哈希判断，会识别并更新。\n")
    else:
        what = ""
    if predecessor and predecessor != "-":
        scope = f"相对 `{predecessor}` 的工具链变更："
    else:
        scope = "工具链变更："

    return f"""# Claude Code {ver} · Termux 原生移植

验收：{verification}
{what}
已有安装运行 `claude update`；首次安装见 [README](https://github.com/wmdhs12138/claude-code-termux#安装)。
本 Release 只附构建凭证，不提供 Claude 二进制。

| 凭证 | 值 |
|---|---|
| 官方 linux-arm64 SHA-256 | `{m.get("claude_linux_arm64_sha256", "?")}` |
| 构建产物 SHA-256（本地构建与之相同） | `{m.get("output_sha256", "?")}` |
| Bun 底座 | `{bun.get("version", "?")}` |
| 工具链提交 | `{commit[:12] or "?"}` |

{scope}

{changelog.strip() or "（无）"}

完整数据见附件 `build-manifest.json`；源码构建：`git checkout {tag} && scripts/build.sh {ver}`。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--predecessor", default="-")
    ap.add_argument("--changelog", required=True)
    a = ap.parse_args()

    try:
        with open(a.changelog) as f:
            changelog = f.read()
    except OSError:
        changelog = ""
    notes = release_notes(load(a.manifest), a.tag, a.commit, a.predecessor, changelog)
    with open(a.out, "w") as f:
        f.write(notes)
    print(f"wrote {a.out} ({len(notes)} bytes)")


if __name__ == "__main__":
    main()
