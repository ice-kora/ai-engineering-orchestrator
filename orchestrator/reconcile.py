"""P2-01.6-9 — state reconciliation + one-step advancement.

Every decision re-reads live facts (Beads status/labels/dependencies, Agent
Mail reservations, git branch/head, stored handover + review payloads). No
in-memory assumptions survive between calls (F4 guarantee: subprocess reads
only). One reconcile executes AT MOST ONE auto action, then re-reads and
reports — no loops, no daemon.

Auto-executable actions (allow-list):
  * READY_FOR_REVIEW -> start Antigravity review (P1 flow, errata-compliant)
  * READY_FOR_PULL   -> mail "Task Available" + emit human ACTION_REQUIRED
Everything else (pull, implement, fix, close, merge) stays with humans/CLI.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from adapters import handover as handover_mod
from adapters.agent_mail import AgentMailAdapter
from adapters.beads import BeadsAdapter
from orchestrator import contracts
from orchestrator.store import Store


class State(str, Enum):
    WAIT_DEPENDENCY = "WAIT_DEPENDENCY"
    READY_FOR_PULL = "READY_FOR_PULL"
    IMPLEMENTING = "IMPLEMENTING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    FIX_REQUIRED = "FIX_REQUIRED"
    READY_TO_CLOSE = "READY_TO_CLOSE"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    ESCALATE = "ESCALATE"


class NextAction(str, Enum):
    WAIT = "WAIT"
    NOTIFY_PULL = "NOTIFY_PULL"                # auto: mail + human ACTION_REQUIRED
    ACTION_REQUIRED_PULL = "ACTION_REQUIRED_PULL"  # human: ZCode /pull-task <id>
    START_REVIEW = "START_REVIEW"              # auto (this step)
    ACTION_REQUIRED_IMPLEMENT = "ACTION_REQUIRED_IMPLEMENT"
    ACTION_REQUIRED_FIX = "ACTION_REQUIRED_FIX"
    ACTION_REQUIRED_CLOSE = "ACTION_REQUIRED_CLOSE"  # human: merge + close
    NONE = "NONE"


@dataclass
class TaskRuntimeState:
    task_key: str
    beads_task_id: str
    state: str
    next_action: str
    beads_status: str = ""
    labels: list[str] = field(default_factory=list)
    deps_remaining: list[str] = field(default_factory=list)
    handover_valid: bool | None = None
    handover_errors: list[str] = field(default_factory=list)
    review_verdict: str | None = None
    review_iteration: int | None = None
    branch: str = ""
    branch_head: str = ""
    merged_to_main: bool = False
    reservation_held: bool = False
    note: str = ""
    action_executed: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Reconciler:
    def __init__(self, store: Store, beads: BeadsAdapter, mail: AgentMailAdapter) -> None:
        self.store = store
        self.beads = beads
        self.mail = mail
        self.repo = Path(beads.repo)

    # ---- live fact readers (fresh subprocess reads every call; F4) ----

    def _bd_show(self, task_id: str) -> dict:
        import json
        proc = self.beads._run("show", task_id, "--json")
        items = json.loads(proc.stdout or "[]")
        return items[0] if items else {}

    def _labels(self, task_id: str) -> list[str]:
        proc = self.beads._run("label", "list", task_id, check=False)
        return [ln.strip().lstrip("- ").strip() for ln in proc.stdout.splitlines()
                if ln.strip().startswith("- ")]

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    def _branch_facts(self, task_id: str) -> tuple[str, str, bool]:
        branch = f"agent/zcode/{task_id}"
        head = self._git("rev-parse", "--verify", branch)
        if head.returncode != 0:
            return "", "", False
        merged = self._git("merge-base", "--is-ancestor", branch, "main")
        return branch, head.stdout.strip()[:12], merged.returncode == 0

    # ---- per-task state computation ----

    def task_state(self, plan: contracts.ExecutionPlan, ptask) -> TaskRuntimeState:
        mapping = self.store.mapping(plan.plan_id)
        task_id = mapping.get(ptask.task_key, "")
        if not task_id:
            return TaskRuntimeState(
                task_key=ptask.task_key, beads_task_id="",
                state=State.BLOCKED.value, next_action=NextAction.NONE.value,
                note="not materialized (apply first)")

        show = self._bd_show(task_id)
        status = show.get("status", "")
        labels = self._labels(task_id)

        deps_remaining: list[str] = []
        for dep_key in ptask.dependencies:
            dep_id = mapping.get(dep_key, "")
            dep_status = self._bd_show(dep_id).get("status", "") if dep_id else ""
            if dep_status != "closed":
                deps_remaining.append(dep_key)

        handover = self.store.handover(task_id)
        handover_valid: bool | None = None
        errors: list[str] = []
        if handover is not None:
            errors = handover_mod.validate_payload(handover, "handover")
            handover_valid = not errors
        review = self.store.review(task_id)
        review_verdict = review.get("verdict") if review else None
        review_iter = review.get("iteration") if review else None
        branch, head, merged = self._branch_facts(task_id)
        reservation = any(r["reason"] == task_id for r in self.mail.active_reservations())

        if status == "closed":
            state, action = State.DONE, NextAction.NONE
        elif status == "open":
            if deps_remaining:
                state, action = State.WAIT_DEPENDENCY, NextAction.WAIT
            else:
                state, action = State.READY_FOR_PULL, NextAction.NOTIFY_PULL
        else:  # in_progress / blocked
            if handover is not None and not handover_valid:
                state, action = State.BLOCKED, NextAction.NONE
            elif review_verdict == "CHANGES_REQUESTED":
                state = State.ESCALATE if (review_iter or 1) >= 3 else State.FIX_REQUIRED
                action = NextAction.ACTION_REQUIRED_FIX
            elif review_verdict == "APPROVED":
                state = State.READY_TO_CLOSE
                action = NextAction.ACTION_REQUIRED_CLOSE
            elif handover_valid:
                state, action = State.READY_FOR_REVIEW, NextAction.START_REVIEW
            else:
                state, action = State.IMPLEMENTING, NextAction.ACTION_REQUIRED_IMPLEMENT

        return TaskRuntimeState(
            task_key=ptask.task_key, beads_task_id=task_id,
            state=state.value, next_action=action.value,
            beads_status=status, labels=labels, deps_remaining=deps_remaining,
            handover_valid=handover_valid, handover_errors=errors,
            review_verdict=review_verdict, review_iteration=review_iter,
            branch=branch, branch_head=head, merged_to_main=merged,
            reservation_held=reservation,
            note="schema-invalid handover: review forbidden" if handover is not None and not handover_valid else "")

    # ---- one-step plan reconcile ----

    def reconcile_plan(self, plan_id: str, execute: bool = True) -> dict:
        plan = self.store.plan(plan_id)
        states = [self.task_state(plan, t) for t in plan.tasks]

        executed = ""
        if execute:
            # exactly ONE auto action per call, review before notify (progress first)
            for ptask, st in zip(plan.tasks, states):
                if st.state == State.READY_FOR_REVIEW.value:
                    executed = self._start_review(plan, ptask, st)
                    break
            if not executed:
                doc = self.store.plan_doc(plan_id) or {}
                notified = set(doc.get("notified", []))
                for ptask, st in zip(plan.tasks, states):
                    if st.state == State.READY_FOR_PULL.value and ptask.task_key not in notified:
                        executed = self._notify_pull(plan_id, ptask, st)
                        break

        states = [self.task_state(plan, t) for t in plan.tasks]  # re-read after action
        all_done = all(s.state == State.DONE.value for s in states)
        if all_done and self.store.plan_status(plan_id) != "DONE":
            self.store.set_plan_status(plan_id, "DONE")

        return {
            "plan_id": plan_id,
            "plan_status": self.store.plan_status(plan_id),
            "action_executed": executed,
            "tasks": [s.to_dict() for s in states],
            "read_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    # ---- auto actions (allow-list of two) ----

    def _notify_pull(self, plan_id: str, ptask, st: TaskRuntimeState) -> str:
        self.mail.send("B", "A", st.beads_task_id,
                       f"[{st.beads_task_id}] Task Available: {ptask.title}",
                       f"Plan {plan_id} task {ptask.task_key} is ready to pull.")
        doc = self.store.plan_doc(plan_id) or {}
        doc.setdefault("notified", []).append(ptask.task_key)
        self.store._write(f"plans/{plan_id}.json", doc)
        return (f"NOTIFIED + ACTION_REQUIRED: ZCode /pull-task {st.beads_task_id} "
                f"(mail thread {st.beads_task_id})")

    def _start_review(self, plan: contracts.ExecutionPlan, ptask, st: TaskRuntimeState) -> str:
        from adapters.review_flow import review_iteration  # local import: agy dependency
        payload = self.store.handover(st.beads_task_id)
        report = review_iteration(repo=self.repo, task=ptask.__dict__,
                                   acceptance=ptask.acceptance_criteria,
                                   handover_payload=payload)
        self.store.save_review(st.beads_task_id, report)
        return (f"START_REVIEW executed: verdict={report.get('verdict')} "
                f"iteration={report.get('iteration')} "
                f"worktree_clean={report.get('_worktree_clean_after_review')}")
