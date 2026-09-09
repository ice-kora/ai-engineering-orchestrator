"""Shared MCP client helpers for Agent Mail integration tests (no extra deps).

Launches the Agent Mail MCP server over stdio (Rust: `mcp-agent-mail`)
with storage redirected into the project sandbox (no C: writes, fully
reversible). Tests use asyncio.run() around plain async funcs.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DATA = PROJECT_ROOT / "sandbox" / "agent-mail-test-data"
MAIL_BIN = shutil.which("mcp-agent-mail")

PROJECT_KEY = "demo-repo"
AGENTS = ("agent-zcode", "agent-antigravity")


def mail_available() -> bool:
    return MAIL_BIN is not None


def server_env() -> dict[str, str]:
    """Storage redirection: everything lands under sandbox/ (project-local)."""
    env = dict(os.environ)
    env.update({
        "STORAGE_ROOT": str(SANDBOX_DATA / "storage"),
        "DATABASE_URL": f"sqlite+aiosqlite:///{(SANDBOX_DATA / 'storage' / 'storage.sqlite3').as_posix()}",
        "XDG_CONFIG_HOME": str(SANDBOX_DATA / "config"),
        "AGENT_NAME": "test-runner",
    })
    return env


def stdio_params():
    from mcp import StdioServerParameters

    return StdioServerParameters(command=MAIL_BIN, args=[], env=server_env())
