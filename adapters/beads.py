"""BeadsAdapter — task state authority via the real `bd` CLI (v1.2.2).

All state changes go through public CLI commands; the underlying Dolt store is
never touched directly (v1.1 §8.1). Claim compensation follows v1.1 §8.3:
status back to `open`, stage/owner labels removed, backoff label added.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from adapters.config import PATHS

TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-[a-z0-9]{3,8}$")


class BeadsError(RuntimeError):
    """A bd invocation failed (message includes stderr)."""


@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: str = ""
    priority: int = 0
    labels: list[str] = field(default_factory=list)

    def target_paths(self, default: list[str] | None = None) -> list[str]:
        """Parse the `Paths: a, b` convention from the description."""
        for line in self.description.splitlines():
            if line.strip().lower().startswith("paths:"):
                rest = line.split(":", 1)[1]
                paths = [p.strip() for p in rest.split(",") if p.strip()]
                if paths:
                    return paths
        return list(default or ["src/**"])

    def acceptance_criteria(self) -> str:
        for line in self.description.splitlines():
            if line.strip().lower().startswith("acceptance:"):
                return line.split(":", 1)[1].strip()
        return ""


class BeadsAdapter:
    def __init__(self, repo: Path, timeout: float = 60) -> None:
        self.repo = Path(repo)
        self.timeout = timeout
        if not (self.repo / ".beads").exists():
            raise BeadsError(f"{self.repo} has no .beads; run `bd init` first (process cwd required, -C refuses)")

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [str(PATHS.bd), *args],
            cwd=str(self.repo), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.timeout,
        )
        if check and proc.returncode != 0:
            raise BeadsError(f"bd {' '.join(args)} failed rc={proc.returncode}:\n{proc.stderr.strip()[:400]}")
        return proc

    # ---- queries ----

    def ready(self) -> list[Task]:
        proc = self._run("ready", "--json")
        return [Task(**{k: item.get(k, "") for k in ("id", "title", "description", "status", "priority")})
                for item in json.loads(proc.stdout or "[]")]

    def get(self, task_id: str) -> Task:
        items = json.loads(self._run("show", task_id, "--json").stdout or "[]")
        if not items:
            raise BeadsError(f"task not found: {task_id}")
        item = items[0]
        return Task(id=item["id"], title=item.get("title", ""),
                    description=item.get("description", ""),
                    status=item.get("status", ""), priority=item.get("priority", 0))

    # ---- lifecycle ----

    def create(self, title: str, priority: int = 1, description: str = "") -> str:
        args = ["create", title, "-p", str(priority)]
        if description:
            args += ["-d", description]
        proc = self._run(*args)
        m = re.search(r"Created issue: (\S+)", proc.stdout)
        if not m:
            raise BeadsError(f"could not parse task id from: {proc.stdout!r}")
        task_id = m.group(1)
        assert TASK_ID_RE.match(task_id), f"unexpected id shape: {task_id}"
        return task_id

    def add_dependency(self, child: str, parent: str) -> None:
        self._run("dep", "add", child, parent)

    def claim(self, task_id: str, actor: str) -> None:
        """Atomic claim (v1.0 §六.A / GPT gate). Raises BeadsError on race loss."""
        self._run("update", task_id, "--claim", "--actor", actor)

    def add_label(self, task_id: str, label: str, actor: str) -> None:
        # single-label form only (multi-label positional misparses colon labels)
        self._run("update", task_id, "--add-label", label, "--actor", actor)

    def remove_label(self, task_id: str, label: str, actor: str) -> None:
        self._run("label", "remove", task_id, label, "--actor", actor)

    def set_status_open(self, task_id: str, actor: str) -> None:
        self._run("update", task_id, "--status", "open", "--actor", actor)

    def compensate_claim(self, task_id: str, actor: str,
                         stage_labels: tuple[str, ...] = ("stage:CLAIMED", "stage:IMPLEMENTING")) -> list[str]:
        """v1.1 §8.3 compensation: revert claim, clean labels, add backoff.

        Returns the list of executed actions (evidence trail).
        """
        evidence: list[str] = []
        self.set_status_open(task_id, actor)
        evidence.append(f"bd update {task_id} --status open --actor {actor}")
        for label in stage_labels + (f"owner:{actor}",):
            proc = self._run("label", "remove", task_id, label, "--actor", actor, check=False)
            if proc.returncode == 0:
                evidence.append(f"bd label remove {task_id} {label}")
        import time
        stamp = time.strftime("%Y%m%dT%H%M%S")
        self._run("update", task_id, "--add-label", f"stage:READY", "--actor", actor, check=False)
        self._run("update", task_id, "--add-label", f"backoff_until:{stamp}", "--actor", actor)
        evidence.append(f"bd update {task_id} --add-label stage:READY backoff_until:{stamp}")
        return evidence

    def close(self, task_id: str, reason: str, actor: str) -> None:
        self._run("close", task_id, "--reason", reason, "--actor", actor)
