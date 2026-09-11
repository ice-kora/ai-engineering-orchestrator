"""P2-04 Final Hotfix T19-T22: C3 persisted-decision replay integrity.

Proves crash-recovery NEVER weakens the normal-path gates:
  schema validation + verdict enum + task_key grounding + fail-closed.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters import handover as hm  # noqa: E402
from adapters.agent_mail import AgentMailAdapter  # noqa: E402
from adapters.beads import BeadsAdapter  # noqa: E402
from orchestrator import contracts  # noqa: E402
from orchestrator.approval import ApprovalGate  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402
from orchestrator.run_controller import RunController  # noqa: E402
from orchestrator.store import Store  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"

pytestmark = pytest.mark.skipif(not (REPO / ".beads").exists(), reason="demo-repo not initialized")


class SpyFinalGate:
    def __init__(self):
        self.calls = 0

    def decide_for_plan(self, ctx):
        self.calls += 1
        raise AssertionError("Codex must NOT be called on replay path")


def full_decision(**over):
    """A complete, valid FinalGateDecision with APPROVED verdict."""
    base = {"verdict": "APPROVED", "summary": "all satisfied",
            "request_coverage": [{"requirement": "cancel reason validation",
                                  "status": "SATISFIED", "evidence": ["tests"]}],
            "findings": []}
    base.update(over)
    return base


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / "store")
    beads = BeadsAdapter(REPO)
    mail = AgentMailAdapter(REPO)
    gate = ApprovalGate(store)
    ids = []
    orig_create = BeadsAdapter.create
    orig_close = BeadsAdapter.close

    def tc(self, title, priority=1, description=""):
        tid = orig_create(self, title, priority, description)
        ids.append(tid)
        return tid

    BeadsAdapter.create = tc
    BeadsAdapter.close = orig_close
    try:
        yield store, beads, mail, gate, ids
    finally:
        BeadsAdapter.create = orig_create
        for tid in ids:
            beads._run("close", tid, "--reason", "hotfix cleanup", check=False)


def _mechanically_done_plan(env):
    """Single-task plan driven to mechanically-DONE with real merge."""
    store, beads, mail, gate, ids = env
    marker = uuid.uuid4().hex[:6]
    plan = contracts.ExecutionPlan(
        plan_id=f"plan-hc{marker}", request_id=f"req-hc{marker}", target_repo=str(REPO),
        summary="s", tasks=[contracts.PlannedTask(
            task_key=f"hc-{marker}", title=f"hc {marker}", description="d",
            target_paths=[f"src/hc-{marker}.py"], acceptance_criteria="criterion met")])
    plan.validate_dag()
    store.save_plan(plan, status="PLANNED")
    store.save_request({"request_id": plan.request_id, "target_repo": str(REPO),
                        "title": "t", "description": "d", "constraints": [],
                        "created_at": "2026-09-11"})
    gate.approve(plan.plan_id, "human")
    Materializer(store, beads, gate).apply(plan)
    tid = store.mapping(plan.plan_id)[f"hc-{marker}"]
    beads.claim(tid, "agent-zcode")

    # real commit + merge
    wt = REPO / "worktrees" / "agent-zcode"
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", str(wt),
                    "-b", f"agent/zcode/{tid}", "main"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(wt), "checkout", "-B", f"agent/zcode/{tid}", "main"],
                   capture_output=True, check=True)
    f = wt / f"src/hc-{marker}.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# {tid}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-m", f"[{tid}] hc"],
                   capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    base = subprocess.run(["git", "-C", str(REPO), "rev-parse", "main"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    h = hm.build_handover(task_id=tid, iteration=1, worktree_path=str(wt),
                          branch_name=f"agent/zcode/{tid}", base_commit=base,
                          head_commit=head, affected_files=[f"src/hc-{marker}.py"],
                          test_command="t", test_exit_code=0, total_tests=1,
                          passed_tests=1, failed_tests=0, output_summary="OK",
                          deliverable_summary="d")
    store.save_handover(tid, h)
    store.save_review(tid, {"schema_version": "1.1.0", "task_id": tid, "iteration": 1,
                            "reviewer": "agent-antigravity", "verdict": "APPROVED",
                            "verified_head_commit": head[:12], "findings": []})
    subprocess.run(["git", "-C", str(REPO), "checkout", "main"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(REPO), "merge", "--no-ff", f"agent/zcode/{tid}",
                    "-m", f"merge [{tid}]"], capture_output=True, check=True)
    beads.close(tid, "done", "test")
    return plan, tid


def _crash_state(store, plan, decision):
    """Simulate: decision persisted + plan stuck at FINAL_GATE_RUNNING."""
    store.save_final_gate(plan.plan_id, decision)
    store.set_plan_status(plan.plan_id, "FINAL_GATE_RUNNING")


# T19: valid APPROVED -> replay -> invariant -> DONE, zero Codex calls
def test_t19_valid_approved_replay_done(env):
    plan, tid = _mechanically_done_plan(env)
    store = env[0]
    _crash_state(store, plan, full_decision())
    spy = SpyFinalGate()
    r = RunController(store, env[1], env[2],
                      final_gate_engine=spy).continue_run(plan.plan_id, max_steps=1)
    assert spy.calls == 0                     # Codex NOT re-called
    assert store.plan_status(plan.plan_id) == "DONE"
    assert any("C3: replaying" in n for n in r.recovery_notes)


# T20: schema-invalid (only verdict) -> NOT DONE -> ESCALATED, zero calls
def test_t20_schema_invalid_not_done(env):
    plan, tid = _mechanically_done_plan(env)
    store = env[0]
    _crash_state(store, plan, {"verdict": "APPROVED"})  # missing required fields
    spy = SpyFinalGate()
    r = RunController(store, env[1], env[2],
                      final_gate_engine=spy).continue_run(plan.plan_id, max_steps=1)
    assert spy.calls == 0
    assert store.plan_status(plan.plan_id) == "ESCALATED"
    assert any("INVALID_PERSISTED_FINAL_GATE_DECISION" in n for n in r.recovery_notes)
    # evidence preserved for forensics
    assert store.final_gate(plan.plan_id) is not None


# T21: valid schema but unknown task_key grounding -> NOT DONE -> ESCALATED
def test_t21_grounding_invalid_not_done(env):
    plan, tid = _mechanically_done_plan(env)
    store = env[0]
    poisoned = full_decision(findings=[
        {"severity": "MAJOR", "task_key": "ghost-task-does-not-exist",
         "description": "refers to a task not in this plan",
         "recommended_action": "investigate"}])
    _crash_state(store, plan, poisoned)
    spy = SpyFinalGate()
    r = RunController(store, env[1], env[2],
                      final_gate_engine=spy).continue_run(plan.plan_id, max_steps=1)
    assert spy.calls == 0
    assert store.plan_status(plan.plan_id) == "ESCALATED"
    assert any("grounding" in n and "unknown task_key" in n for n in r.recovery_notes)


# T22: valid FOLLOWUP_REQUIRED -> FINAL_FIX_REQUIRED, zero calls
def test_t22_valid_followup_replay(env):
    plan, tid = _mechanically_done_plan(env)
    store = env[0]
    followup = {"verdict": "FOLLOWUP_REQUIRED", "summary": "gap",
                "request_coverage": [{"requirement": "csv export",
                                      "status": "MISSING", "evidence": []}],
                "findings": [{"severity": "BLOCKER", "description": "csv missing",
                              "recommended_action": "replan"}]}
    _crash_state(store, plan, followup)
    spy = SpyFinalGate()
    r = RunController(store, env[1], env[2],
                      final_gate_engine=spy).continue_run(plan.plan_id, max_steps=1)
    assert spy.calls == 0
    assert store.plan_status(plan.plan_id) == "FINAL_FIX_REQUIRED"
    assert any("C3: replaying" in n for n in r.recovery_notes)
