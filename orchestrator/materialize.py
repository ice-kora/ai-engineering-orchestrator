"""P2-01.4/.5 — idempotent DAG materialization (ExecutionPlan -> Beads).

Idempotency & crash safety rest on three legs:
  1. the task_key->beads_task_id ledger (written after EVERY single create);
  2. every created Beads task carries the label ``plan:<plan_id>`` so truth is
     verifiable in Beads itself (never trust the ledger alone);
  3. re-apply only creates tasks whose task_key is absent from the ledger AND
     absent from Beads (label scan) — a ledger/Beads mismatch heals toward
     Beads (the authority) instead of duplicating.

Dependencies are wired with `bd dep add child parent` AFTER both ends exist.
The Beads DB is never touched directly.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from adapters.beads import BeadsAdapter, BeadsError
from orchestrator import contracts
from orchestrator.approval import ApprovalGate
from orchestrator.store import Store

PLAN_LABEL_PREFIX = "plan:"


@dataclass
class ApplyReport:
    created: list[str]          # task_keys created this run
    reused: list[str]           # task_keys already materialized
    dependencies_wired: int
    beads_task_count: int       # verified live count of plan-labeled tasks

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Materializer:
    def __init__(self, store: Store, beads: BeadsAdapter, gate: ApprovalGate) -> None:
        self.store = store
        self.beads = beads
        self.gate = gate

    # ---- truth helpers ----

    def _plan_labeled_ids(self, plan_id: str) -> dict[str, str]:
        """Live Beads truth: {task_key: beads_id} recovered from task bodies.

        Every materialized task embeds `TaskKey: <key>` / `Plan: <plan_id>` in
        its description, so recovery works via `bd list --desc-contains` even
        if the ledger file was lost. Beads is the authority; the ledger only
        accelerates.
        """
        import json
        proc = self.beads._run("list", "--json", "--desc-contains", f"Plan: {plan_id}")
        items = json.loads(proc.stdout or "[]")
        found: dict[str, str] = {}
        for item in items:
            for line in (item.get("description") or "").splitlines():
                if line.strip().startswith("TaskKey:"):
                    key = line.split(":", 1)[1].strip()
                    if key:
                        found[key] = item["id"]
        return found

    def apply(self, plan: contracts.ExecutionPlan) -> ApplyReport:
        if self.store.plan_status(plan.plan_id) == "DONE":
            raise BeadsError("plan already DONE")
        self.gate.require_approved(plan.plan_id)  # P2-01.3 gate

        ledger = dict(self.store.mapping(plan.plan_id))
        live = self._plan_labeled_ids(plan.plan_id)  # Beads is the authority
        existing = {**live, **ledger}  # ledger fills gaps Beads labels can't show

        created: list[str] = []
        reused: list[str] = []
        for task in plan.tasks:
            if task.task_key in existing:
                reused.append(task.task_key)
                if task.task_key not in ledger:
                    self.store.put_mapping(plan.plan_id, task.task_key, existing[task.task_key])
                continue
            description = (f"TaskKey: {task.task_key}\n"
                           f"Plan: {plan.plan_id}\n"
                           f"Paths: {', '.join(task.target_paths)}\n"
                           f"Acceptance: {task.acceptance_criteria}\n"
                           f"Executor: {task.executor_role} / Reviewer: {task.reviewer_role}\n"
                           f"Risk: {task.risk_level}\n\n{task.description}")
            beads_id = self.beads.create(task.title, priority=1, description=description)
            self.beads.add_label(beads_id, f"{PLAN_LABEL_PREFIX}{plan.plan_id}", "orchestrator")
            self.store.put_mapping(plan.plan_id, task.task_key, beads_id)  # BEFORE next create
            existing[task.task_key] = beads_id
            created.append(task.task_key)

        wired = 0
        for task in plan.tasks:
            for dep in task.dependencies:
                self.beads.add_dependency(existing[task.task_key], existing[dep])
                wired += 1

        final_live = self._plan_labeled_ids(plan.plan_id)
        if len(final_live) != len(plan.tasks):
            raise BeadsError(
                f"materialization incomplete: {len(final_live)}/{len(plan.tasks)} in Beads; "
                "re-run apply to recover")
        if self.store.plan_status(plan.plan_id) != "APPLIED":
            self.store.set_plan_status(plan.plan_id, "APPLIED")
        return ApplyReport(created=created, reused=reused, dependencies_wired=wired,
                           beads_task_count=len(final_live))
