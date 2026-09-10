"""P2-01.2 — PlannerPort. Deterministic planners only in P2-01.

GPTPlanner is a reserved stub (P2-02): no API calls, no keys, no router.
A planner maps UserRequest -> ExecutionPlan and does nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from orchestrator import contracts


class PlannerError(RuntimeError):
    pass


class PlannerPort(Protocol):
    def plan(self, request: contracts.UserRequest) -> contracts.ExecutionPlan: ...


class JsonPlanner:
    """Deterministic: reads a validated ExecutionPlan document (dict or file).

    The plan's request_id/target_repo are forced to match the incoming request
    so the planner cannot smuggle cross-request state.
    """

    def __init__(self, plan_doc: dict | str | Path) -> None:
        if isinstance(plan_doc, (str,)) and str(plan_doc).strip().startswith("{"):
            import json
            doc = json.loads(plan_doc)
        elif isinstance(plan_doc, dict):
            doc = plan_doc
        else:
            doc = contracts.load_json(plan_doc)
        self._doc = doc

    def plan(self, request: contracts.UserRequest) -> contracts.ExecutionPlan:
        doc = dict(self._doc)
        doc["request_id"] = request.request_id
        doc["target_repo"] = request.target_repo
        plan = contracts.ExecutionPlan.from_dict(doc)
        plan.validate_dag()
        return plan


class GPTPlanner:
    """RESERVED for P2-02 — deliberately unusable in P2-01."""

    def plan(self, request: contracts.UserRequest) -> contracts.ExecutionPlan:
        raise NotImplementedError(
            "GPTPlanner arrives in P2-02; P2-01 forbids LLM API calls, keys and routers"
        )
