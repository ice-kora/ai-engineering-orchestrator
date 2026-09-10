"""PullFlow — the /pull-task entry (P1-01) + Claim/Lease Saga (P1-02).

Sequence (GPT P1 spec):
  ready queue -> pick task -> ATOMIC claim -> derive target paths ->
  reserve via Agent Mail (JSON-parsed, never exit-code) ->
    conflict/failure: release partial grants -> Beads compensation
    (status open + label cleanup + backoff label) -> CODING FORBIDDEN
    success: enter PER_AGENT worktree branch agent/zcode/<task-id> and
             emit the execution context.

Every step appends to `evidence` so the whole run is auditable.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from adapters.agent_mail import AgentMailAdapter, ReservationResult
from adapters.beads import BeadsAdapter, Task

BACKOFF_SECONDS = 30  # v1.1 §3.1 initial backoff (P1 keeps it symbolic: label only)


class SagaCompensated(RuntimeError):
    """Raised when reservation conflicted; claim was compensated — do NOT code."""

    def __init__(self, message: str, evidence: list[str]) -> None:
        super().__init__(message)
        self.evidence = evidence


@dataclass
class ExecutionPlan:
    task: Task
    actor: str
    worktree: Path
    branch: str
    reservation: ReservationResult
    base_commit: str
    evidence: list[str] = field(default_factory=list)


class PullFlow:
    def __init__(self, repo: Path, actor: str = "agent-zcode", agent_role: str = "A") -> None:
        self.repo = Path(repo)
        self.actor = actor
        self.agent_role = agent_role
        self.beads = BeadsAdapter(repo)
        self.mail = AgentMailAdapter(repo)
        self.evidence: list[str] = []

    def _note(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.evidence.append(f"[{stamp}] {msg}")

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"git {args} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout.strip()

    # ---- P1-01 + P1-02 ----

    def pull_task(self, task_id: str | None = None) -> ExecutionPlan:
        # 1) ready queue
        ready = self.beads.ready()
        if not ready:
            raise RuntimeError("ready queue is empty")
        self._note(f"bd ready -> {len(ready)} task(s): {[t.id for t in ready]}")

        # 2) select
        task = None
        if task_id:
            task = next((t for t in ready if t.id == task_id), None)
            if task is None:
                raise RuntimeError(f"task {task_id} not in ready queue")
        else:
            task = sorted(ready, key=lambda t: t.priority)[0]
        self._note(f"selected {task.id} (P{task.priority}) {task.title!r}")

        # 3) atomic claim
        try:
            self.beads.claim(task.id, self.actor)
        except Exception as exc:  # race lost or state error — no side effects to undo
            self._note(f"claim FAILED (no reservation made): {exc}")
            raise
        self._note(f"bd update {task.id} --claim --actor {self.actor} -> OK")
        self.beads.add_label(task.id, "stage:CLAIMED", self.actor)

        # 4-6) reservation with structured-result semantics + saga compensation
        paths = task.target_paths()
        reason = task.id  # unified id convention
        result = self.mail.reserve(self.agent_role, paths, reason, ttl_seconds=900)
        self._note(f"am reserve reason={reason} paths={paths} -> "
                   f"granted={len(result.granted)} conflicts={len(result.conflicts)}")
        if not result.success:
            evidence = list(self.evidence)
            # release partial grants of THIS acquire
            if result.granted:
                self.mail.release_all(self.agent_role)
                evidence.append("am file_reservations release (partial grants of this acquire)")
            # Beads compensation (CLI-only, v1.1 §8.3)
            comp = self.beads.compensate_claim(task.id, self.actor,
                                               stage_labels=("stage:CLAIMED",))
            evidence.extend(comp)
            raise SagaCompensated(
                f"reservation conflicted for {task.id}; claim compensated; CODING FORBIDDEN "
                f"(backoff {BACKOFF_SECONDS}s+). Conflicts: "
                f"{[c.get('path') for c in result.conflicts]}", evidence)

        # 7) PER_AGENT worktree + task branch (no forced main checkout, v1.1 §7.1)
        worktree = self.repo / "worktrees" / "agent-zcode"
        if not worktree.exists():
            self._git("worktree", "add", "worktrees/agent-zcode", "-b", "agent/zcode/home", "main")
            self._note("created PER_AGENT worktree worktrees/agent-zcode (home branch)")
        branch = f"agent/zcode/{task.id}"
        base_commit = self._git("rev-parse", "main")
        self._git("checkout", "-B", branch, "main", cwd=worktree)
        self._note(f"git checkout -B {branch} main (base {base_commit[:9]}) in {worktree}")

        self.beads.add_label(task.id, "stage:IMPLEMENTING", self.actor)
        return ExecutionPlan(task=task, actor=self.actor, worktree=worktree, branch=branch,
                             reservation=result, base_commit=base_commit,
                             evidence=list(self.evidence))

    # ---- later stages ----

    def finish(self, task_id: str) -> None:
        """Release reservation and close task (called after VERIFIED + merge)."""
        self.mail.release_all(self.agent_role)
        self._note(f"am file_reservations release --agent {self.actor} (reason={task_id})")
