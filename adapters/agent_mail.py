"""AgentMailAdapter — advisory-lease + messaging via the real `am` CLI (v0.3.35).

Channel decision (P1-00): ZCODE_AGENT_MAIL_MCP = FALLBACK_CLI — the server's
stdio MCP endpoint rejected every standard client/handshake we tried
(python SDK + raw JSON-RPC across five protocol versions); the `am` CLI is the
vendor-blessed surface and was fully verified in P0.

Hard rules encoded here (P0 evidence):
- NEVER trust the reserve exit code: a conflicting reserve returns JSON
  {granted: [], conflicts: [...]} and still exits 0. Success = parsed result.
- clap strict ordering: options BEFORE positional arguments.
- actor identity: auto-generated adjective+noun names persisted per role.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from adapters.config import PATHS, agent_mail_env, identity_store

ROLE_PROGRAMS = {"A": ("zcode", "glm-5.3"), "B": ("antigravity", "gemini-3")}


class AgentMailError(RuntimeError):
    pass


@dataclass
class ReservationResult:
    granted: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)

    @property
    def success(self) -> bool:
        # v1.1 §3.1: any conflict => whole acquire failed, regardless of exit code
        return bool(self.granted) and not self.conflicts


class AgentMailAdapter:
    def __init__(self, project: Path, timeout: float = 120) -> None:
        self.project = Path(project)
        self.project_key = self.project.resolve().as_posix()
        self.timeout = timeout
        self._identities: dict[str, str] = {}

    # ---- process plumbing ----

    def _am(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [str(PATHS.am), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=agent_mail_env(), timeout=self.timeout,
        )
        if check and proc.returncode != 0:
            raise AgentMailError(f"am {' '.join(args)} rc={proc.returncode}:\n{proc.stderr.strip()[:400]}")
        return proc

    def _am_json(self, *args: str) -> dict | list:
        proc = self._am(*args)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AgentMailError(
                f"am {' '.join(args)} printed non-JSON:\n{proc.stdout[:400]}") from exc

    # ---- identity (auto-generated names, persisted per role) ----

    def identities(self) -> dict[str, str]:
        if self._identities:
            return self._identities
        store = identity_store()
        mapping: dict[str, str] = {}
        if store.exists():
            mapping = json.loads(store.read_text(encoding="utf-8"))
        for role, (program, model) in ROLE_PROGRAMS.items():
            if role not in mapping:
                out = self._am_json("agents", "register", "--project", self.project_key,
                                    "--program", program, "--model", model, "--json")
                mapping[role] = out["name"]
                store.parent.mkdir(parents=True, exist_ok=True)
                store.write_text(json.dumps(mapping, indent=1), encoding="utf-8")
        self._identities = mapping
        return mapping

    def agent(self, role: str) -> str:
        return self.identities()[role]

    # ---- reservations (advisory lease) ----

    def reserve(self, agent_role: str, paths: list[str], reason: str,
                ttl_seconds: int = 900, exclusive: bool = True) -> ReservationResult:
        """Reserve paths; the JSON body decides success (never the exit code)."""
        args = ["file_reservations", "reserve",
                "--ttl", str(max(60, ttl_seconds)), "--reason", reason]
        if exclusive:
            args.append("--exclusive")
        # positional args go last (clap strict ordering)
        proc = self._am(*args, self.project_key, self.agent(agent_role), *paths, check=False)
        try:
            body = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AgentMailError(
                f"reserve returned non-JSON (rc={proc.returncode}):\n"
                f"stdout={proc.stdout[:300]}\nstderr={proc.stderr[:300]}") from exc
        result = ReservationResult(granted=body.get("granted", []) or [],
                                   conflicts=body.get("conflicts", []) or [])
        return result

    def release_all(self, agent_role: str) -> int:
        proc = self._am("file_reservations", "release", self.project_key, self.agent(agent_role))
        m = "Released"
        return 1 if m in proc.stdout else 0

    # ---- messaging ----

    def send(self, from_role: str, to_role: str, thread_id: str, subject: str, body: str) -> dict:
        return self._am_json("mail", "send",
                             "--project", self.project_key,
                             "--from", self.agent(from_role), "--to", self.agent(to_role),
                             "--subject", subject, "--body", body,
                             "--thread-id", thread_id, "--json")

    def inbox(self, agent_role: str) -> dict | list:
        return self._am_json("mail", "inbox",
                             "--project", self.project_key,
                             "--agent", self.agent(agent_role), "--json")
