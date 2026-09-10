"""P2-03 T1-T15: final gate / arbitration state-integrity tests.

Real Beads/Mail/git components; semantic engines are fakes (real Codex runs
are the gated demo). T13 exercises CodexCLIInvoker command shape via mocked
subprocess; T7/T8 exercise the REAL engine retry loop via a fake invoker.
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
from orchestrator.codex_invoker import CodexCLIInvoker  # noqa: E402
from orchestrator.decisions import validate_decision  # noqa: E402
from orchestrator.final_gate import (  # noqa: E402
    CodexArbitrationEngine, CodexFinalGateEngine, DecisionError,
    FinalGateContextBuilder, FinalGateForbidden,
)
from orchestrator.gpt_planner import GPTPlannerError  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402
from orchestrator.planner import JsonPlanner  # noqa: E402
from orchestrator.reconcile import Reconciler, State  # noqa: E402
from orchestrator.store import Store  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"
WT = REPO / "worktrees" / "agent-zcode"

pytestmark = pytest.mark.skipif(not (REPO / ".beads").exists(), reason="demo-repo not initialized")


# ---------- helpers ----------

def approved_payload(**over):
    base = {"verdict": "APPROVED", "summary": "all requirements covered",
            "request_coverage": [{"requirement": "cancel reason validation",
                                  "status": "SATISFIED", "evidence": ["tests"]}],
            "findings": []}
    base.update(over)
    return base


def followup_payload():
    return {"verdict": "FOLLOWUP_REQUIRED", "summary": "gap remains",
            "request_coverage": [{"requirement": "csv export", "status": "MISSING",
                                  "evidence": []}],
            "findings": [{"severity": "BLOCKER", "description": "csv export not delivered",
                          "recommended_action": "replan with an export task"}]}


class FakeFinalGate:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.calls = []

    def decide_for_plan(self, ctx):
        self.calls.append(ctx)
        d = self.decisions.pop(0)
        if isinstance(d, Exception):
            raise d
        return d


class FakeArbitration:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def decide_for_task(self, ctx):
        self.calls.append(ctx)
        return self.decision


def make_plan(store: Store) -> contracts.ExecutionPlan:
    marker = uuid.uuid4().hex[:6]
    plan = contracts.ExecutionPlan(
        plan_id=f"plan-fg{marker}", request_id=f"req-fg{marker}", target_repo=str(REPO),
        summary="final gate probe",
        tasks=[contracts.PlannedTask(
            task_key=f"fg-task-{marker}", title=f"fg probe {marker}",
            description="d", target_paths=[f"src/fg-{marker}.py"],
            acceptance_criteria="criterion met")])
    plan.validate_dag()
    store.save_plan(plan, status="PLANNED")
    store.save_request({"request_id": plan.request_id, "target_repo": str(REPO),
                        "title": "fg request", "description": "d",
                        "constraints": [], "created_at": "2026-09-10"})
    return plan


def complete_task(env, plan, verdict="APPROVED", iteration=1, close=True) -> str:
    """Drive one task to mechanically-DONE: claim, real commit, handover,
    review, merge, release, close."""
    store, beads, mail, gate, *_ = env
    tid = store.mapping(plan.plan_id)[plan.tasks[0].task_key]
    beads.claim(tid, "agent-zcode")
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", str(WT),
                    "-b", f"agent/zcode/{tid}", "main"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(WT), "checkout", "-B", f"agent/zcode/{tid}", "main"],
                   capture_output=True, check=True)
    f = WT / plan.tasks[0].target_paths[0]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# {tid}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(WT), "add", "-A"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(WT), "commit", "-m", f"[{tid}] impl"],
                   capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(WT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    h = hm.build_handover(
        task_id=tid, iteration=iteration, worktree_path=str(WT),
        branch_name=f"agent/zcode/{tid}",
        base_commit=subprocess.run(["git", "-C", str(REPO), "rev-parse", "main~1" if False else "rev-parse" ],
                                   capture_output=True).stdout.decode() or "0" * 40,
        head_commit=head, affected_files=[plan.tasks[0].target_paths[0]],
        test_command="python -m unittest", test_exit_code=0, total_tests=1,
        passed_tests=1, failed_tests=0, output_summary="OK",
        deliverable_summary="implemented criterion")
    # fix base_commit properly
    base = subprocess.run(["git", "-C", str(REPO), "rev-parse", "main"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    h["git_context"]["base_commit"] = base
    assert hm.validate_payload(h, "handover") == []
    store.save_handover(tid, h)
    store.save_review(tid, {"schema_version": "1.1.0", "task_id": tid,
                            "iteration": iteration, "reviewer": "agent-antigravity",
                            "verdict": verdict, "verified_head_commit": head[:12],
                            "findings": [] if verdict == "APPROVED"
                            else [{"severity": "MAJOR", "file_path": "src/x.py",
                                   "issue_type": "LOGIC_BUG", "description": "bad",
                                   "actionable_fix": "fix"}]})
    subprocess.run(["git", "-C", str(REPO), "checkout", "main"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(REPO), "merge", "--no-ff", f"agent/zcode/{tid}",
                    "-m", f"merge [{tid}]"], capture_output=True, check=True)
    if close:
        beads.close(tid, "probe done", "test")
    return tid


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / "store")
    beads = BeadsAdapter(REPO)
    mail = AgentMailAdapter(REPO)
    gate = ApprovalGate(store)
    created_branches: list[str] = []
    orig_close = BeadsAdapter.close
    created_ids: list[str] = []

    def tracking_close(self, tid, reason, actor):
        created_ids.append(tid)
        return orig_close(self, tid, reason, actor)

    BeadsAdapter.close = tracking_close
    try:
        yield store, beads, mail, gate, created_branches, created_ids
    finally:
        BeadsAdapter.close = orig_close


def mechanically_done_env(env, verdict="APPROVED", iteration=1):
    store, beads, mail, gate, *_ = env
    plan = make_plan(store)
    gate.approve(plan.plan_id, "human")
    Materializer(store, beads, gate).apply(plan)
    tid = complete_task(env, plan, verdict=verdict, iteration=iteration)
    return plan, tid


# ---------- T1: mechanically DONE -> READY_FOR_FINAL_GATE, not DONE ----------

def test_t1_ready_for_final_gate_not_done(env):
    plan, _ = mechanically_done_env(env)
    store, beads, mail, *_ = env
    recon = Reconciler(store, beads, mail)  # no engine wired
    r = recon.reconcile_plan(plan.plan_id)
    assert r["plan_status"] == "READY_FOR_FINAL_GATE"
    assert r["plan_status"] != "DONE"
    assert "READY_FOR_FINAL_GATE" in r["action_executed"]


# ---------- T2: APPROVED -> host invariant -> DONE ----------

def test_t2_final_approved_done(env):
    plan, _ = mechanically_done_env(env)
    store, beads, mail, *_ = env
    engine = FakeFinalGate(approved_payload())
    recon = Reconciler(store, beads, mail, final_gate_engine=engine)
    recon.reconcile_plan(plan.plan_id)              # call 1: -> READY_FOR_FINAL_GATE
    r = recon.reconcile_plan(plan.plan_id)          # call 2: runs the gate (one step)
    assert r["plan_status"] == "DONE"
    assert "host invariant OK" in r["action_executed"]
    assert store.final_gate(plan.plan_id)["verdict"] == "APPROVED"


# ---------- T3: FOLLOWUP_REQUIRED -> FINAL_FIX_REQUIRED, no auto tasks ----------

def test_t3_followup_final_fix_required(env):
    plan, tid = mechanically_done_env(env)
    store, beads, mail, *_ = env
    before = len(json.loads(beads._run("list", "--json").stdout))
    engine = FakeFinalGate(followup_payload())
    recon = Reconciler(store, beads, mail, final_gate_engine=engine)
    recon.reconcile_plan(plan.plan_id)
    r = recon.reconcile_plan(plan.plan_id)
    assert r["plan_status"] == "FINAL_FIX_REQUIRED"
    assert "ACTION_REQUIRED: REPLAN" in r["action_executed"]
    after = len(json.loads(beads._run("list", "--json").stdout))
    assert after == before  # no Beads task auto-created


# ---------- T4: missing machine evidence -> Codex NOT called ----------

def test_t4_missing_evidence_codex_not_called(env):
    plan, tid = mechanically_done_env(env)
    store, beads, mail, *_ = env
    engine = FakeFinalGate(approved_payload())
    recon = Reconciler(store, beads, mail, final_gate_engine=engine)
    recon.reconcile_plan(plan.plan_id)               # call 1: -> READY_FOR_FINAL_GATE
    assert store.plan_status(plan.plan_id) == "READY_FOR_FINAL_GATE"
    (store.root / "reviews" / f"{tid}.json").unlink()  # destroy machine evidence NOW
    r = recon.reconcile_plan(plan.plan_id)           # call 2: gate must not run
    assert engine.calls == []                        # semantic engine never invoked
    # evidence gone -> task is INCONSISTENT_CLOSED (not mechanically DONE) -> the
    # gate branch is unreachable; status stays put for human inspection
    assert r["plan_status"] == "READY_FOR_FINAL_GATE"
    assert "FINAL_GATE" not in r["action_executed"]


# ---------- T5: stale review excluded from context ----------

def test_t5_stale_review_excluded(env):
    plan, tid = mechanically_done_env(env)
    store, beads, mail, *_ = env
    review = store.review(tid)
    review["iteration"] = 99                            # stale vs handover iteration
    store.save_review(tid, review)
    with pytest.raises(FinalGateForbidden):
        FinalGateContextBuilder(store, beads, mail).build(plan.plan_id)


# ---------- T6: invented task keys -> decision rejected ----------

def test_t6_invented_task_rejected(env):
    plan, _ = mechanically_done_env(env)
    store, beads, mail, *_ = env
    poisoned = approved_payload(findings=[
        {"severity": "MAJOR", "task_key": "task-does-not-exist",
         "description": "ghost task", "recommended_action": "x"}])
    validate_decision(poisoned, "final_gate")           # schema-valid but ungrounded
    engine = FakeFinalGate(poisoned)
    recon = Reconciler(store, beads, mail, final_gate_engine=engine)
    recon.reconcile_plan(plan.plan_id)
    r = recon.reconcile_plan(plan.plan_id)
    assert r["plan_status"] == "ESCALATED"
    assert "REJECTED (invented task keys" in r["action_executed"]
    assert store.final_gate(plan.plan_id)["error"] == "ungrounded decision"


# ---------- T7/T8: real engine retry loop via fake invoker ----------

class FakeInvoker:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def invoke(self, *, system_prompt, user_content, schema, schema_name="x"):
        self.calls += 1
        p = self.payloads.pop(0)
        if isinstance(p, Exception):
            raise p
        return {"payload": p, "usage": {"total_tokens": 1},
                "meta": {"model": "fake", "reasoning_effort": "high"}}


def test_t7_malformed_then_valid_retry_within_2(env):
    plan, _ = mechanically_done_env(env)
    invoker = FakeInvoker([{"verdict": "GARBAGE"}, approved_payload()])
    engine = CodexFinalGateEngine(invoker=invoker)
    store, beads, mail, *_ = env
    recon = Reconciler(store, beads, mail, final_gate_engine=engine)
    recon.reconcile_plan(plan.plan_id)
    r = recon.reconcile_plan(plan.plan_id)
    assert invoker.calls == 2 and r["plan_status"] == "DONE"


def test_t8_double_invalid_escalates(env):
    plan, _ = mechanically_done_env(env)
    invoker = FakeInvoker([{"verdict": "GARBAGE"}, {"verdict": "STILL_GARBAGE"}])
    engine = CodexFinalGateEngine(invoker=invoker)
    store, beads, mail, *_ = env
    recon = Reconciler(store, beads, mail, final_gate_engine=engine)
    recon.reconcile_plan(plan.plan_id)
    r = recon.reconcile_plan(plan.plan_id)
    assert invoker.calls == 2
    assert r["plan_status"] == "ESCALATED"           # fail closed -> human
    assert "failed closed" in r["action_executed"]


# ---------- T9/T10: UserRequest persistence ----------

def test_t9_request_immutable_idempotent(env):
    store = env[0]
    doc = {"request_id": "req-imm-1", "target_repo": str(REPO), "title": "t",
           "description": "d", "constraints": [], "created_at": "2026-09-10"}
    store.save_request(doc)
    store.save_request(dict(doc))                     # idempotent replay
    assert store.request("req-imm-1") == doc


def test_t10_request_conflict_blocked(env):
    store = env[0]
    doc = {"request_id": "req-conf-1", "target_repo": str(REPO), "title": "t",
           "description": "d", "constraints": [], "created_at": "2026-09-10"}
    store.save_request(doc)
    different = dict(doc, title="CHANGED")
    with pytest.raises(Store.RequestConflict):
        store.save_request(different)
    assert store.request("req-conf-1") == doc          # original intact


# ---------- T11/T12: arbitration ----------

def _escalated_task_env(env):
    store, beads, mail, gate, *_ = env
    plan = make_plan(store)
    gate.approve(plan.plan_id, "human")
    Materializer(store, beads, gate).apply(plan)
    tid = complete_task(env, plan, verdict="CHANGES_REQUESTED", iteration=3, close=False)
    review = store.review(tid)
    review["iteration"] = 3
    store.save_review(tid, review)
    # task stays in_progress with 3x CHANGES_REQUESTED -> ESCALATE state
    return plan, tid


def test_t11_three_reviews_trigger_arbitration(env):
    plan, tid = _escalated_task_env(env)
    store, beads, mail, *_ = env
    arb = FakeArbitration({"verdict": "HUMAN_DECISION_REQUIRED",
                           "rationale": "conflicting valid positions",
                           "binding_directives": []})
    recon = Reconciler(store, beads, mail, arbitration_engine=arb)
    r = recon.reconcile_plan(plan.plan_id)
    assert arb.calls, "arbitration engine was not invoked"
    stored = store.arbitration(tid)
    assert stored["verdict"] == "HUMAN_DECISION_REQUIRED"
    assert "ACTION_REQUIRED: human decision" in r["action_executed"]


def test_t12_accept_risk_does_not_close(env):
    plan, tid = _escalated_task_env(env)
    store, beads, mail, *_ = env
    arb = FakeArbitration({"verdict": "ACCEPT_RISK_RECOMMENDATION",
                           "rationale": "findings minor vs criteria",
                           "binding_directives": [],
                           "risk_note": "residual risk acceptable"})
    recon = Reconciler(store, beads, mail, arbitration_engine=arb)
    r = recon.reconcile_plan(plan.plan_id)
    assert "recommendation ONLY" in r["action_executed"]
    assert beads.get(tid).status != "closed"          # never auto-closed
    st = {t["task_key"]: t for t in r["tasks"]}[plan.tasks[0].task_key]
    assert st["state"] != State.DONE.value


# ---------- T13: invoker runs read-only + ephemeral ----------

def test_t13_invoker_readonly_ephemeral(monkeypatch):
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        calls["stdin"] = kw.get("input", "")
        for i, a in enumerate(cmd):
            if a == "-o":
                Path(cmd[i + 1]).write_text('{"ok": true}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    inv = CodexCLIInvoker()
    out = inv.invoke(system_prompt="SYS", user_content="USER",
                     schema={"type": "object"}, schema_name="probe")
    joined = " ".join(calls["cmd"])
    assert "-s read-only" in joined and "--ephemeral" in joined
    assert "--output-schema" in joined and calls["cmd"][-1] == "-"
    assert calls["stdin"].startswith("SYS")
    assert out["payload"] == {"ok": True}


# ---------- T14: JsonPlanner regression ----------

def test_t14_json_planner_regression(env):
    store = env[0]
    request = contracts.UserRequest(
        request_id="req-t14-json", target_repo=str(REPO),
        title="json planner regression", description="d", constraints=[])
    doc = {"plan_id": "plan-t14-json", "request_id": "req-t14-json",
           "target_repo": str(REPO), "summary": "s",
           "tasks": [{"task_key": "t14-task", "title": "regression task",
                      "description": "d", "target_paths": ["src/calculator.py"],
                      "acceptance_criteria": "criterion met", "dependencies": [],
                      "executor_role": "zcode", "reviewer_role": "antigravity",
                      "risk_level": "LOW"}],
           "risks": [], "assumptions": [], "requires_human_approval": True}
    plan = JsonPlanner(doc).plan(request)
    plan.validate_dag()
    store.save_request(request.to_dict())
    store.save_plan(plan, status="PLANNED")
    assert store.plan_status("plan-t14-json") == "PLANNED"
    assert store.request("req-t14-json")["title"] == "json planner regression"


# ---------- T15: P2-01 reconciler regression (pull boundary unchanged) ----------

def test_t15_p201_reconciler_regression(env):
    store, beads, mail, gate, *_ = env
    plan = make_plan(store)
    gate.approve(plan.plan_id, "human")
    Materializer(store, beads, gate).apply(plan)
    recon = Reconciler(store, beads, mail)
    r = recon.reconcile_plan(plan.plan_id)   # execute: NOTIFY_PULL expected
    st = r["tasks"][0]
    assert st["state"] == State.READY_FOR_PULL.value
    assert "NOTIFIED + ACTION_REQUIRED: ZCode /pull-task" in r["action_executed"]
    assert r["plan_status"] == "APPLIED"     # no premature final gate
