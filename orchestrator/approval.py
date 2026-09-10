"""P2-01.3 — Human Approval Gate.

A generated plan is PLANNED and MUST NOT materialize until an explicit,
separately-invoked approve(). No component may self-approve: approve/reject
are only reachable from the CLI by a human action.
"""

from __future__ import annotations

from orchestrator.store import Store, StoreError


class ApprovalError(RuntimeError):
    pass


class ApprovalGate:
    def __init__(self, store: Store) -> None:
        self.store = store

    def approve(self, plan_id: str, approved_by: str = "human") -> dict:
        if self.store.plan_status(plan_id) != "PLANNED":
            raise ApprovalError(
                f"plan {plan_id} is {self.store.plan_status(plan_id)}; only PLANNED can be approved")
        if approved_by.strip().lower() in ("auto", "system", "agent", "orchestrator", ""):
            raise ApprovalError("automatic self-approval is forbidden (approved_by must be a human)")
        record = self.store.save_approval(plan_id, "APPROVED", approved_by)
        self.store.set_plan_status(plan_id, "APPROVED")
        return record

    def reject(self, plan_id: str, approved_by: str = "human", reason: str = "") -> dict:
        if self.store.plan_status(plan_id) not in ("PLANNED", "APPROVED"):
            raise ApprovalError(f"plan {plan_id} cannot be rejected from {self.store.plan_status(plan_id)}")
        record = self.store.save_approval(plan_id, "REJECTED", approved_by)
        self.store.set_plan_status(plan_id, "REJECTED")
        return record

    def require_approved(self, plan_id: str) -> dict:
        record = self.store.approval(plan_id)
        if not record or record["decision"] != "APPROVED":
            raise ApprovalError(f"plan {plan_id} is not approved — materialization forbidden")
        return record
