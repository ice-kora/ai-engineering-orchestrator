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

# P2-03: final-gate lifecycle states appended (history-compatible: existing
# PLANNED/APPLIED/DONE docs keep working; nothing jumps APPLIED -> DONE anymore)
PLAN_STATUSES = ("PLANNED", "APPROVED", "REJECTED", "APPLIED",
                 "READY_FOR_FINAL_GATE", "FINAL_GATE_RUNNING",
                 "FINAL_FIX_REQUIRED", "ESCALATED", "DONE")


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        for sub in ("plans", "approvals", "mapping", "handovers", "reviews",
                    "requests", "final_gates", "arbitrations", "review_history"):
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

    # ---- immutable UserRequest persistence (P2-03 §3) ----

    class RequestConflict(StoreError):
        """Same request_id persisted with DIFFERENT content — never overwrite."""

    def save_request(self, request: dict) -> None:
        existing = self._read(f"requests/{request['request_id']}.json")
        if existing is not None:
            if existing == request:
                return                                   # idempotent replay
            raise self.RequestConflict(
                f"request_id {request['request_id']} already stored with different content; "
                "BLOCKED (no silent overwrite)")
        self._write(f"requests/{request['request_id']}.json", request)

    def request(self, request_id: str) -> dict | None:
        return self._read(f"requests/{request_id}.json")

    # ---- final gate / arbitration records (P2-03) ----

    def save_final_gate(self, plan_id: str, payload: dict) -> None:
        self._write(f"final_gates/{plan_id}.json", payload)

    def final_gate(self, plan_id: str) -> dict | None:
        return self._read(f"final_gates/{plan_id}.json")

    def save_arbitration(self, beads_task_id: str, payload: dict) -> None:
        self._write(f"arbitrations/{beads_task_id}.json", payload)

    def arbitration(self, beads_task_id: str) -> dict | None:
        return self._read(f"arbitrations/{beads_task_id}.json")

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

    class ReviewHistoryConflict(StoreError):
        """Same task_id+iteration persisted with DIFFERENT content."""

    def save_review(self, beads_task_id: str, payload: dict) -> None:
        """Latest-review entry (compat) + append-only immutable history.

        History rule: same task_id+iteration with identical content is
        idempotent; different content raises ReviewHistoryConflict — history
        is never silently overwritten (P2-03 hotfix Fix-2).
        """
        iteration = payload.get("iteration")
        if isinstance(iteration, int) and iteration >= 1:
            hist_path = f"review_history/{beads_task_id}/{iteration}.json"
            existing = self._read(hist_path)
            if existing is not None and existing != payload:
                raise self.ReviewHistoryConflict(
                    f"review history {beads_task_id}#{iteration} already exists "
                    "with different content; BLOCKED (no silent overwrite)")
            if existing is None:
                (self.root / "review_history" / beads_task_id).mkdir(
                    parents=True, exist_ok=True)
                self._write(hist_path, payload)
        self._write(f"reviews/{beads_task_id}.json", payload)

    def review(self, beads_task_id: str) -> dict | None:
        return self._read(f"reviews/{beads_task_id}.json")

    def review_history(self, beads_task_id: str) -> dict[int, dict]:
        """All persisted iterations: {iteration: payload}."""
        folder = self.root / "review_history" / beads_task_id
        if not folder.exists():
            return {}
        out: dict[int, dict] = {}
        for f in sorted(folder.glob("*.json")):
            try:
                out[int(f.stem)] = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
        return out
