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

    class AmbiguousTaskKey(BeadsError):
        """Same TaskKey found on multiple live Beads tasks — refuses to guess."""

    def _live_candidates(self, plan_id: str) -> dict[str, list[str]]:
        """Live Beads truth: {task_key: [beads_id, ...]} (duplicate-aware)."""
        import json
        proc = self.beads._run("list", "--json", "--desc-contains", f"Plan: {plan_id}")
        items = json.loads(proc.stdout or "[]")
        found: dict[str, list[str]] = {}
        for item in items:
            for line in (item.get("description") or "").splitlines():
                if line.strip().startswith("TaskKey:"):
                    key = line.split(":", 1)[1].strip()
                    if key:
                        found.setdefault(key, []).append(item["id"])
        return found

    def _ledger_id_verifies(self, beads_id: str, task_key: str, plan_id: str) -> bool:
        """A ledger id is trusted ONLY if the live Beads task exists and its
        body carries the matching TaskKey/Plan markers."""
        import json
        proc = self.beads._run("show", beads_id, "--json", check=False)
        if proc.returncode != 0:
            return False
        items = json.loads(proc.stdout or "[]")
        if not items:
            return False
        desc = items[0].get("description") or ""
        return f"TaskKey: {task_key}" in desc and f"Plan: {plan_id}" in desc

    def _reconcile_mapping(self, plan_id: str, task_keys: list[str]):
        """Beads-authority reconciliation (P2-01 hotfix Fix-1).

        Returns (canonical {task_key: beads_id}, evidence[], to_create[]).
        Per task_key:
          live == ledger          -> REUSE
          live, ledger missing    -> repair ledger from live
          live, ledger different  -> LIVE WINS, overwrite ledger, evidence
          ledger only             -> verify against the real Beads body;
                                     stale/wrong -> purge the ledger entry
          multiple live ids       -> AmbiguousTaskKey (BLOCKED, never guess)
        """
        live = self._live_candidates(plan_id)
        ledger = dict(self.store.mapping(plan_id))
        canonical: dict[str, str] = {}
        evidence: list[str] = []
        to_create: list[str] = []

        for key in task_keys:
            live_ids = live.get(key, [])
            if len(live_ids) > 1:
                raise self.AmbiguousTaskKey(
                    f"TaskKey {key!r} maps to multiple live Beads tasks {live_ids} "
                    f"for plan {plan_id}: BLOCKED (manual arbitration required)")
            live_id = live_ids[0] if live_ids else None
            ledger_id = ledger.get(key)

            if live_id and ledger_id == live_id:
                canonical[key] = live_id                      # REUSE
            elif live_id and not ledger_id:
                self.store.put_mapping(plan_id, key, live_id)
                evidence.append(f"ledger repaired from live: {key} -> {live_id}")
                canonical[key] = live_id
            elif live_id and ledger_id != live_id:
                self.store.put_mapping(plan_id, key, live_id)
                evidence.append(f"LIVE WINS over wrong ledger: {key} {ledger_id} -> {live_id}")
                canonical[key] = live_id
            elif ledger_id:
                if self._ledger_id_verifies(ledger_id, key, plan_id):
                    canonical[key] = ledger_id
                    evidence.append(f"ledger id verified against Beads body: {key} -> {ledger_id}")
                else:
                    ledger.pop(key)
                    self.store._write(f"mapping/{plan_id}.json", ledger)
                    evidence.append(f"stale ledger purged: {key} !-> {ledger_id} (no such/mismatched task)")
                    to_create.append(key)
            else:
                to_create.append(key)
        return canonical, evidence, to_create

    def apply(self, plan: contracts.ExecutionPlan) -> ApplyReport:
        if self.store.plan_status(plan.plan_id) == "DONE":
            raise BeadsError("plan already DONE")
        self.gate.require_approved(plan.plan_id)  # P2-01.3 gate

        canonical, evidence, to_create = self._reconcile_mapping(
            plan.plan_id, [t.task_key for t in plan.tasks])

        created: list[str] = []
        reused: list[str] = []
        for task in plan.tasks:
            if task.task_key in canonical:
                reused.append(task.task_key)
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
            canonical[task.task_key] = beads_id
            created.append(task.task_key)

        # dependency wiring uses ONLY the post-reconciliation canonical mapping
        wired = 0
        for task in plan.tasks:
            for dep in task.dependencies:
                self.beads.add_dependency(canonical[task.task_key], canonical[dep])
                wired += 1

        # guard: no cross-plan dependencies — every dep edge of a plan task
        # must point back into this plan's canonical id set
        import json as _json
        plan_ids = set(canonical.values())
        for key, bid in canonical.items():
            show = self.beads._run("show", bid, "--json")
            deps = (_json.loads(show.stdout or "[]")[0].get("dependencies") or [])
            for dep in deps:  # bd emits dependency objects {id, title, ...}
                dep_id = dep.get("id") if isinstance(dep, dict) else str(dep)
                if dep_id not in plan_ids:
                    raise BeadsError(f"cross-plan dependency detected: {key}({bid}) -> {dep_id}")

        final_live = self._live_candidates(plan.plan_id)
        if sum(1 for ids in final_live.values() if ids) != len(plan.tasks):
            raise BeadsError(
                f"materialization incomplete: {len(final_live)}/{len(plan.tasks)} in Beads; "
                "re-run apply to recover")
        if self.store.plan_status(plan.plan_id) != "APPLIED":
            self.store.set_plan_status(plan.plan_id, "APPLIED")
        return ApplyReport(created=created, reused=reused, dependencies_wired=wired,
                           beads_task_count=len(final_live))
