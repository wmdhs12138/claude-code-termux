"""Paths shared by the test modules (unittest discover puts tests/ on sys.path)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "tui_smoke.py"
FETCH = ROOT / "scripts" / "fetch-claude.sh"
BUILD = ROOT / "scripts" / "build.sh"
UPDATE = ROOT / "scripts" / "update.sh"
RUNTIME = ROOT / "runtime"
SELF_UPDATE = RUNTIME / "self-update.sh"
ASSEMBLE_RUNTIME = ROOT / "tools" / "assemble_runtime.py"
EMBED_PRELOAD = ROOT / "tools" / "embed_preload.py"
INSTALL = ROOT / "install.sh"
INSTALL_APPROVED = ROOT / "scripts" / "install-approved.sh"
ENSURE_BUN = ROOT / "scripts" / "ensure-bun-base.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
BIONIC_CI = ROOT / ".github" / "ci" / "bionic-build.sh"
