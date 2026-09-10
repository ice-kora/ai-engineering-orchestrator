"""Orchestrator JSON ledger (crash-safe, append-per-write).

Holds the machine-authoritative orchestrator-side state that is NOT Beads'
business: plans, approvals, task_key<->beads_task_id mapping, handover
payloads, review reports. Beads remains the sole task-status authority.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from orchestrator import contracts

PLAN_STATUSES = ("PLANNED", "APPROVED", "REJECTED", "APPLIED", "DONE")


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        for sub in ("plans", "approvals", "mapping", "handovers", "reviews"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- primitives ----

    def _write(self, rel: str, payload: Any) -> None:
        path = self.root / rel
        tmp = path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(path)  # atomic on same volume

    def _read(self, rel: str) -> Any | None:
        path = self.root / rel
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ---- plans ----

    def save_plan(self, plan: contracts.ExecutionPlan, status: str = "PLANNED") -> None:
        if status not in PLAN_STATUSES:
            raise StoreError(f"bad plan status {status}")
        doc = {"status": status, "plan": plan.to_dict()}
        self._write(f"plans/{plan.plan_id}.json", doc)

    def plan_doc(self, plan_id: str) -> dict | None:
        return self._read(f"plans/{plan_id}.json")

    def plan(self, plan_id: str) -> contracts.ExecutionPlan:
        doc = self.plan_doc(plan_id)
        if not doc:
            raise StoreError(f"plan not found: {plan_id}")
        return contracts.ExecutionPlan.from_dict(doc["plan"])

    def plan_status(self, plan_id: str) -> str:
        doc = self.plan_doc(plan_id)
        if not doc:
            raise StoreError(f"plan not found: {plan_id}")
        return doc["status"]

    def set_plan_status(self, plan_id: str, status: str) -> None:
        doc = self.plan_doc(plan_id)
        if not doc:
            raise StoreError(f"plan not found: {plan_id}")
        doc["status"] = status
        self._write(f"plans/{plan_id}.json", doc)

    # ---- approvals (P2-01.3) ----

    def save_approval(self, plan_id: str, decision: str, approved_by: str) -> dict:
        record = {
            "plan_id": plan_id,
            "decision": decision,  # APPROVED | REJECTED
            "approved_by": approved_by,
            "approved_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._write(f"approvals/{plan_id}.json", record)
        return record

    def approval(self, plan_id: str) -> dict | None:
        return self._read(f"approvals/{plan_id}.json")

    # ---- idempotency ledger: task_key -> beads_task_id ----

    def mapping(self, plan_id: str) -> dict[str, str]:
        return self._read(f"mapping/{plan_id}.json") or {}

    def put_mapping(self, plan_id: str, task_key: str, beads_task_id: str) -> None:
        mapping = self.mapping(plan_id)
        mapping[task_key] = beads_task_id
        self._write(f"mapping/{plan_id}.json", mapping)

    # ---- handovers / reviews (task-id keyed) ----

    def save_handover(self, beads_task_id: str, payload: dict) -> None:
        self._write(f"handovers/{beads_task_id}.json", payload)

    def handover(self, beads_task_id: str) -> dict | None:
        return self._read(f"handovers/{beads_task_id}.json")

    def save_review(self, beads_task_id: str, payload: dict) -> None:
        self._write(f"reviews/{beads_task_id}.json", payload)

    def review(self, beads_task_id: str) -> dict | None:
        return self._read(f"reviews/{beads_task_id}.json")
