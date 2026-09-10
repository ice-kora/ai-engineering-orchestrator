"""Unit: orchestrator contracts (schemas + DAG validation)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator import contracts  # noqa: E402


def request_dict(**over):
    base = {"request_id": "req-demo-abc123", "target_repo": "sandbox/demo-repo",
            "title": "demo request", "description": "d", "constraints": [], "created_at": "2026-09-10"}
    base.update(over)
    return base


def task(key, deps=()):
    return {"task_key": key, "title": f"task {key}", "description": "d",
            "target_paths": ["src/x.py"], "acceptance_criteria": "criterion met",
            "dependencies": list(deps), "executor_role": "zcode",
            "reviewer_role": "antigravity", "risk_level": "LOW"}


def plan_dict(**over):
    base = {"plan_id": "plan-demo-abc123", "request_id": "req-demo-abc123",
            "target_repo": "sandbox/demo-repo", "summary": "s",
            "tasks": [task("task-a"), task("task-b", ["task-a"])],
            "risks": [], "assumptions": [], "requires_human_approval": True}
    base.update(over)
    return base


def test_request_and_plan_roundtrip():
    req = contracts.UserRequest.from_dict(request_dict())
    plan = contracts.ExecutionPlan.from_dict(plan_dict())
    plan.validate_dag()
    assert req.request_id and plan.tasks[1].dependencies == ["task-a"]


@pytest.mark.parametrize("mutation", [
    {"request_id": "bad id"},           # pattern
    {"title": "x"},                     # minLength
    {"constraints": "none"},            # type
])
def test_request_invalid(mutation):
    with pytest.raises(contracts.ContractError):
        contracts.UserRequest.from_dict(request_dict(**mutation))


@pytest.mark.parametrize("mutation", [
    {"plan_id": "X"},                    # pattern
    {"requires_human_approval": "yes"},  # type
    {"tasks": []},                       # minItems
])
def test_plan_invalid(mutation):
    with pytest.raises(contracts.ContractError):
        contracts.ExecutionPlan.from_dict(plan_dict(**mutation))


def test_task_roles_enum():
    bad = task("task-a"); bad["executor_role"] = "devops"
    with pytest.raises(contracts.ContractError):
        contracts.ExecutionPlan.from_dict(plan_dict(tasks=[bad]))


def test_dag_unknown_dependency_and_cycle():
    with pytest.raises(contracts.ContractError, match="unknown"):
        plan = contracts.ExecutionPlan.from_dict(plan_dict(tasks=[task("task-b", ["ghost-key"])]))
        plan.validate_dag()
    cyc = plan_dict(tasks=[task("task-a", ["task-b"]), task("task-b", ["task-a"])])
    plan = contracts.ExecutionPlan.from_dict(cyc)
    with pytest.raises(contracts.ContractError, match="cycle"):
        plan.validate_dag()
