"""Agent Mail test helpers — CLI (`am`) channel.

Why CLI instead of the python MCP SDK (spec-vs-reality, 2026-09-09):
mcp_agent_mail_rust v0.3.35 rejects the python `mcp` 2.2.0 client at
initialize ("Request does not match the connection's negotiated MCP protocol
era"). The `am` CLI exposes the same capability surface without protocol
negotiation and is the P0 verification channel; the MCP-era finding itself is
recorded evidence (input for the A5 ZCode-MCP decision).

Other empirical rules baked in here:
- agent names MUST be adjective+noun (e.g. "QuickSilver"); descriptive role
  names like "agent-zcode" are rejected → roles live in program/model fields.
- clap strict ordering: options BEFORE positional args on subcommands.
- a conflicting reserve prints JSON {granted:[], conflicts:[...]} and a human
  note but STILL EXITS 0 — callers must parse JSON, never trust exit codes.
- CLI file_reservations has renew/release but NO force-release (MCP-only).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DATA = PROJECT_ROOT / "sandbox" / "agent-mail-test-data"
DEMO_REPO = (PROJECT_ROOT / "sandbox" / "demo-repo").as_posix()

_Dir = Path(r"D:\Software\ai-orchestrator\agent-mail")
AM_BIN = shutil.which("am") or str(_Dir / "am.exe") if (_Dir / "am.exe").exists() else shutil.which("am")
SERVER_BIN = str(_Dir / "mcp-agent-mail.exe")

import json as _json

IDENTITY_STORE = SANDBOX_DATA / "identities.json"


def get_identities() -> tuple[str, str]:
    """Resolve (zcode-role, antigravity-role) agent names for demo-repo.

    The platform REJECTS hand-picked names (adjective+noun auto-generation
    only, descriptive blocklist) — so we auto-generate once and persist the
    mapping program->name in sandbox/agent-mail-test-data/identities.json.
    """
    mapping: dict[str, str] = {}
    if IDENTITY_STORE.exists():
        mapping = _json.loads(IDENTITY_STORE.read_text(encoding="utf-8"))
    for role, program, model in (("A", "zcode", "glm-5.3"), ("B", "antigravity", "gemini-3")):
        if role not in mapping:
            out = am_json("agents", "register", "--project", DEMO_REPO,
                          "--program", program, "--model", model, "--json")
            mapping[role] = out["name"]
            IDENTITY_STORE.parent.mkdir(parents=True, exist_ok=True)
            IDENTITY_STORE.write_text(_json.dumps(mapping, indent=1), encoding="utf-8")
    return mapping["A"], mapping["B"]


def mail_available() -> bool:
    return bool(AM_BIN) and Path(AM_BIN).exists()


def server_env() -> dict[str, str]:
    """Storage redirection: everything lands under sandbox/ (project-local)."""
    env = dict(os.environ)
    env.update({
        "STORAGE_ROOT": str(SANDBOX_DATA / "storage"),
        "DATABASE_URL": f"sqlite+aiosqlite:///{(SANDBOX_DATA / 'storage' / 'storage.sqlite3').as_posix()}",
        "XDG_CONFIG_HOME": str(SANDBOX_DATA / "config"),
    })
    return env


def am(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    assert AM_BIN, "am binary not found"
    return subprocess.run(
        [AM_BIN, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=server_env(), timeout=timeout,
    )


def am_json(*args: str, timeout: float = 120) -> dict:
    """Run am and parse JSON output (args must place options before positionals)."""
    proc = am(*args, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"am {' '.join(args)} failed rc={proc.returncode}:\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"am {' '.join(args)} printed non-JSON (stdout follows):\n{proc.stdout[:800]}"
        ) from exc


def _port_open(port: int = 8765) -> bool:
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_http_server() -> bool:
    """Attested reads need the HTTP server; reuse or spawn one (sandbox env)."""
    if _port_open():
        return True
    if not Path(SERVER_BIN).exists():
        return False
    subprocess.Popen(
        [SERVER_BIN, "serve", "--no-tui"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=server_env(), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    import time
    for _ in range(20):
        if _port_open():
            return True
        time.sleep(0.5)
    return False


def register_identities() -> tuple[str, str]:
    """Idempotent: ensure both identities exist; return their names."""
    return get_identities()
