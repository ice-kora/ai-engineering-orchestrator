"""Shared paths and environment for P1 adapters (Windows-first, D-drive installs)."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_REPO = PROJECT_ROOT / "sandbox" / "demo-repo"

_BD_DIR = Path(r"D:\Software\ai-orchestrator\beads")
_MAIL_DIR = Path(r"D:\Software\ai-orchestrator\agent-mail")
_AGY_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin"


class PATHS:
    bd = _BD_DIR / "bd.exe"
    am = _MAIL_DIR / "am.exe"
    mail_server = _MAIL_DIR / "mcp-agent-mail.exe"
    agy = _AGY_DIR / "agy.exe"


def agent_mail_env() -> dict[str, str]:
    """Agent Mail storage redirection: everything stays in the project sandbox."""
    data = PROJECT_ROOT / "sandbox" / "agent-mail-test-data"
    env = dict(os.environ)
    env.update({
        "STORAGE_ROOT": str(data / "storage"),
        "DATABASE_URL": f"sqlite+aiosqlite:///{(data / 'storage' / 'storage.sqlite3').as_posix()}",
        "XDG_CONFIG_HOME": str(data / "config"),
    })
    return env


def identity_store() -> Path:
    return PROJECT_ROOT / "sandbox" / "agent-mail-test-data" / "identities.json"
