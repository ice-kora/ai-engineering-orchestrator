"""P2-01 mandatory failure tests F1-F6 (real components, unique plan per test)."""

import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters.agent_mail import AgentMailAdapter  # noqa: E402
from adapters.beads import BeadsAdapter  # noqa: E402
from orchestrator import contracts  # noqa: E402
from orchestrator.approval import ApprovalGate  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402
from orchestrator.reconcile import Reconciler, State  # noqa: E402
from orchestrator.store import Store  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"

pytestmark = pytest.mark.skipif(not (REPO / ".beads").exists(), reason="demo-repo not initialized")


def make_plan(store: Store, n_tasks: int = 2) -> contracts.ExecutionPlan:
    marker = uuid.uuid4().hex[:6]
    tasks = [
        contracts.PlannedTask(
            task_key=f"f{i}-{marker}", title=f"failure-probe {i} {marker}",
            description="probe task", target_paths=[f"src/f{i}-{marker}.py"],
            acceptance_criteria="probe",
            dependencies=[f"f{i-1}-{marker}"] if i else [])
        for i in range(n_tasks)
    ]
    plan = contracts.ExecutionPlan(
        plan_id=f"plan-f{marker}", request_id=f"req-f{marker}", target_repo=str(REPO),
        summary="failure probes", tasks=tasks)
    plan.validate_dag()
    store.save_plan(plan, status="PLANNED")
    return plan


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / "store")
    beads = BeadsAdapter(REPO)
    mail = AgentMailAdapter(REPO)
    gate = ApprovalGate(store)
    created: list[str] = []

    orig_create = BeadsAdapter.create

    def tracking_create(self, title, priority=1, description=""):
        tid = orig_create(self, title, priority, description)
        created.append(tid)
        return tid

    BeadsAdapter.create = tracking_create
    try:
        yield store, beads, mail, gate
    finally:
        BeadsAdapter.create = orig_create
        for tid in created:
            beads._run("close", tid, "--reason", "test cleanup", check=False)


def _applied(env, plan):
    store, beads, mail, gate = env
    gate.approve(plan.plan_id, "human")
    return Materializer(store, beads, gate).apply(plan)


# ---- F1 duplicate apply ----

def test_f1_duplicate_apply(env):
    plan = make_plan(env[0], n_tasks=2)
    r1 = _applied(env, plan)
    assert len(r1.created) == 2 and r1.beads_task_count == 2
    r2 = Materializer(env[0], env[1], env[3]).apply(plan)  # env: store, beads, mail, gate
    assert r2.created == []                # zero duplicated tasks
    assert len(r2.reused) == 2
    assert r2.beads_task_count == 2        # Beads still holds exactly N


# ---- F2 partial materialization crash + recovery ----

def test_f2_partial_apply_crash_recovery(env, monkeypatch):
    plan = make_plan(env[0], n_tasks=3)
    store, beads, mail, gate = env
    gate.approve(plan.plan_id, "human")

    orig_create = BeadsAdapter.create.__wrapped__ if hasattr(BeadsAdapter.create, "__wrapped__") else None
    real_create = beads.create.__self__  # noqa: F841
    from adapters.beads import BeadsAdapter as BA

    calls = {"n": 0}
    real = BA.create

    def crash_after_two(self, title, priority=1, description=""):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("simulated crash mid-materialization")
        return real(self, title, priority, description)

    monkeypatch.setattr(BA, "create", crash_after_two)
    with pytest.raises(RuntimeError):
        Materializer(store, beads, gate).apply(plan)
    assert calls["n"] == 3  # two created, third crashed

    monkeypatch.setattr(BA, "create", real)
    r = Materializer(store, beads, gate).apply(plan)  # safe recovery
    assert sorted(r.created) == sorted([t.task_key for t in plan.tasks][2:])  # only the missing one
    assert r.beads_task_count == 3  # total still N — no duplicates


# ---- F3 dependency gating ----

def test_f3_dependency_gate(env):
    plan = make_plan(env[0], n_tasks=2)
    _applied(env, plan)
    store, beads, mail, gate = env
    recon = Reconciler(store, beads, mail)
    r = recon.reconcile_plan(plan.plan_id, execute=False)
    s = {t["task_key"]: t for t in r["tasks"]}
    keys = sorted(s)
    upstream, downstream = keys[0], keys[1]

    assert s[upstream]["state"] == State.READY_FOR_PULL.value
    assert s[downstream]["state"] == State.WAIT_DEPENDENCY.value

    # bd's own ready queue agrees: downstream NOT ready while upstream open
    ready_ids = {t.id for t in beads.ready()}
    mapping = store.mapping(plan.plan_id)
    assert mapping[downstream] not in ready_ids

    beads.close(mapping[upstream], "F3 close upstream", "test")
    ready_ids = {t.id for t in beads.ready()}
    assert mapping[downstream] in ready_ids, "downstream must become ready after upstream closes"

    r = recon.reconcile_plan(plan.plan_id, execute=False)
    s = {t["task_key"]: t for t in r["tasks"]}
    assert s[downstream]["state"] == State.READY_FOR_PULL.value


# ---- F4 stale in-memory state ----

def test_f4_stale_state_reread(env):
    plan = make_plan(env[0], n_tasks=1)
    _applied(env, plan)
    store, beads, mail, gate = env
    recon = Reconciler(store, beads, mail)
    mapping = store.mapping(plan.plan_id)
    tid = mapping[plan.tasks[0].task_key]

    r1 = recon.reconcile_plan(plan.plan_id, execute=False)
    assert r1["tasks"][0]["state"] == State.READY_FOR_PULL.value

    beads.close(tid, "external close between reconciles", "test")  # mutate truth externally

    r2 = recon.reconcile_plan(plan.plan_id, execute=False)  # same Reconciler instance
    assert r2["tasks"][0]["state"] == State.DONE.value, "reconcile must re-read, not cache"


# ---- F5 invalid handover must NOT start review ----

def test_f5_invalid_handover_gates_review(env):
    plan = make_plan(env[0], n_tasks=1)
    _applied(env, plan)
    store, beads, mail, gate = env
    recon = Reconciler(store, beads, mail)
    tid = store.mapping(plan.plan_id)[plan.tasks[0].task_key]
    beads.claim(tid, "agent-zcode")

    invalid = {"task_id": tid, "iteration": 1}  # missing required fields
    store.save_handover(tid, invalid)

    r = recon.reconcile_plan(plan.plan_id)  # execute=True
    assert r["tasks"][0]["state"] == State.BLOCKED.value
    assert "review forbidden" in r["tasks"][0]["note"]
    assert r["action_executed"] == ""  # no review started
    assert store.review(tid) is None     # and nothing recorded


# ---- F6 CHANGES_REQUESTED -> FIX_REQUIRED, not close ----

def test_f6_changes_requested_fix_required(env):
    plan = make_plan(env[0], n_tasks=1)
    _applied(env, plan)
    store, beads, mail, gate = env
    recon = Reconciler(store, beads, mail)
    ptask = plan.tasks[0]
    tid = store.mapping(plan.plan_id)[ptask.task_key]
    beads.claim(tid, "agent-zcode")

    handover = {
        "schema_version": "1.1.0", "task_id": tid, "iteration": 1,
        "git_context": {"worktree_path": str(REPO), "branch_name": f"agent/zcode/{tid}",
                        "base_commit": "0123456789abcdef0123456789abcdef01234567",
                        "head_commit": "fedcba9876543210fedcba9876543210fedcba98"},
        "affected_files": ["src/x.py"],
        "test_evidence": {"command": "x", "exit_code": 0, "total_tests": 1, "passed_tests": 1,
                          "failed_tests": 0, "output_summary": "OK"},
        "deliverable_summary": "d", "known_risks": [],
    }
    from adapters import handover as hm
    assert hm.validate_payload(handover, "handover") == []
    store.save_handover(tid, handover)

    report = {"schema_version": "1.1.0", "task_id": tid, "iteration": 1,
              "reviewer": "agent-antigravity", "verdict": "CHANGES_REQUESTED",
              "verified_head_commit": "fedcba98",
              "findings": [{"severity": "MAJOR", "file_path": "src/x.py",
                            "issue_type": "LOGIC_BUG", "description": "bug",
                            "actionable_fix": "fix it"}]}
    store.save_review(tid, report)

    r = recon.reconcile_plan(plan.plan_id)
    assert r["tasks"][0]["state"] == State.FIX_REQUIRED.value
    assert r["tasks"][0]["next_action"] == "ACTION_REQUIRED_FIX"
    assert beads.get(tid).status != "closed"  # never closed on CHANGES_REQUESTED
