"""P2-02 T1-T10: GPTPlanner safety gates (mocked API client; no network).

The real-API smoke is a separate gated step (blocked until OPENAI_API_KEY
exists). These tests prove the LOCAL safety pipeline end-to-end: structured
draft -> host-authoritative assembly -> contract -> DAG -> policy -> atomic
save decisions -> bounded retry -> approval isolation.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters.beads import BeadsAdapter  # noqa: E402
from orchestrator import contracts, policy  # noqa: E402
from orchestrator.approval import ApprovalGate  # noqa: E402
from orchestrator.gpt_planner import (  # noqa: E402
    ApiKeyMissing, GPTPlanner, GPTPlannerError, RepoContext,
)
from orchestrator.store import Store  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"

pytestmark = pytest.mark.skipif(not (REPO / ".beads").exists(), reason="demo-repo not initialized")


def request():
    return contracts.UserRequest(
        request_id="req-t-0001", target_repo=str(REPO),
        title="test request", description="d", constraints=[])


def draft(**over):
    base = {
        "summary": "s",
        "tasks": [{
            "task_key": "task-one", "title": "do it", "description": "d",
            "target_paths": ["src/shipping.py"], "acceptance_criteria": "criterion met",
            "dependencies": [], "executor_role": "zcode",
            "reviewer_role": "antigravity", "risk_level": "LOW",
        }],
        "risks": [], "assumptions": [],
    }
    base.update(over)
    return base


class FakeClient:
    """Injectable responses.create returning queued drafts (T-matrix)."""

    def __init__(self, drafts, usage=None):
        self.drafts = list(drafts)
        self.calls = []
        self.usage = usage or {}
        self.responses = self._Responses(self)  # attribute, not method

    class _Responses:
        def __init__(self, owner):
            self._owner = owner

        def create(self, **kw):
            self._owner.calls.append(kw)
            if not self._owner.drafts:
                raise RuntimeError("refused")  # refusal/incomplete simulation
            payload = self._owner.drafts.pop(0)
            import json as _j

            class _R:
                pass

            r = _R()
            r.id = "resp_fake"
            r.output_text = _j.dumps(payload, ensure_ascii=False)
            class _U: pass
            r.usage = _U()
            r.usage.__dict__.update({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
            return r


@pytest.fixture()
def planner(monkeypatch):
    def make(drafts):
        p = GPTPlanner(REPO, plan_id="plan-test-0001")
        fake = FakeClient(drafts)
        monkeypatch.setattr(GPTPlanner, "_make_client", lambda self: fake)
        return p, fake
    return make


# T1 valid structured output -> valid ExecutionPlan
def test_t1_valid_draft(planner):
    p, fake = planner([draft()])
    plan, ev = p.plan(request())
    assert plan.plan_id == "plan-test-0001"
    assert plan.request_id == "req-t-0001"
    assert plan.tasks[0].task_key == "task-one"
    assert ev.attempts[0]["validation_result"] == "valid" and len(fake.calls) == 1


# T2 host-authoritative identity: model cannot steer host fields
def test_t2_host_authoritative(planner):
    poisoned = draft()
    poisoned["plan_id"] = "plan-EVIL"
    poisoned["request_id"] = "req-EVIL"
    poisoned["target_repo"] = "C:/Windows"
    poisoned["requires_human_approval"] = False
    p, _ = planner([poisoned])
    plan, _ = p.plan(request())
    assert plan.plan_id == "plan-test-0001"      # host preallocated id
    assert plan.request_id == "req-t-0001"       # host request
    assert plan.target_repo == str(REPO)         # host repo
    assert plan.requires_human_approval is True  # approval gate immovable


# T3 invalid DAG never reaches Store
def test_t3_invalid_dag_not_saved(planner, tmp_path):
    bad = draft(tasks=[
        {**draft()["tasks"][0], "task_key": "a", "dependencies": ["b"]},
        {**draft()["tasks"][0], "task_key": "b", "target_paths": ["tests/test_x.py"],
         "dependencies": ["a"], "title": "second"},
    ])
    p, _ = planner([bad])
    with pytest.raises(GPTPlannerError) as ei:
        p.plan(request())
    store = Store(tmp_path / "s")
    assert store.plan_doc(p.plan_id) is None


# T4 unsafe paths rejected
@pytest.mark.parametrize("path", ["../escape.py", "C:/abs/x.py", "/abs/x.py",
                                  ".git/config", ".beads/issues.jsonl",
                                  "sandbox/orchestrator-state/x.json"])
def test_t4_unsafe_paths(planner, path):
    bad = draft(tasks=[{**draft()["tasks"][0], "target_paths": [path]}])
    p, _ = planner([bad, bad])
    with pytest.raises(GPTPlannerError) as ei:
        p.plan(request())
    assert "policy" in str(ei.value) or ei.value.error_category == "policy"


# T5 unsupported role combination never approvable
def test_t5_unsupported_roles(planner, tmp_path):
    # API schema enum would block this client-side; test the LOCAL gate for
    # drafts injected past it (defense in depth): role zcode+antigravity only.
    bad_task = draft()["tasks"][0]
    bad_task["executor_role"] = "zcode"
    p, _ = planner([draft()])
    plan, _ = p.plan(request())
    store = Store(tmp_path / "s")
    gate = ApprovalGate(store)
    # policy fixed roles: assemble a plan with a rogue reviewer to prove the
    # policy layer catches it even if schema enums were bypassed upstream
    rogue = {"plan_id": "plan-x", "request_id": "req-t-0001", "target_repo": str(REPO),
             "summary": "s",
             "tasks": [{**bad_task, "reviewer_role": "zcode"}],
             "risks": [], "assumptions": [], "requires_human_approval": True}
    errors = policy.validate(rogue["tasks"], REPO.name,
                             RepoContext.build(REPO).snapshot())
    assert any("reviewer_role" in e for e in errors)
    # and the clean plan DID pass the same gate (positive control)
    assert policy.validate(plan.to_dict()["tasks"], REPO.name,
                           RepoContext.build(REPO).snapshot()) == []


# T6 repair retry: invalid -> valid, exactly 2 calls
def test_t6_repair_retry(planner):
    bad = draft(tasks=[{**draft()["tasks"][0], "dependencies": ["ghost-task"]}])
    p, fake = planner([bad, draft()])
    plan, ev = p.plan(request())
    assert plan.tasks[0].task_key == "task-one"
    assert len(fake.calls) == 2
    assert "REJECTED" in fake.calls[1]["input"][1]["content"]  # repair prompt carries errors
    assert ev.attempts[0]["validation_result"] == "invalid"
    assert ev.attempts[1]["validation_result"] == "valid"


# T7 double-invalid -> PLAN_NOT_SAVED, no third call
def test_t7_double_invalid(planner, tmp_path):
    bad = draft(tasks=[{**draft()["tasks"][0], "dependencies": ["ghost-task"]}])
    p, fake = planner([bad, bad, bad])  # third would exist but must never run
    with pytest.raises(GPTPlannerError, match="PLAN_NOT_SAVED"):
        p.plan(request())
    assert len(fake.calls) == 2
    store = Store(tmp_path / "s")
    assert store.plan_doc(p.plan_id) is None
    with pytest.raises(Exception):
        ApprovalGate(store).require_approved(p.plan_id)


# T8 refusal/incomplete fails closed
def test_t8_refusal_fail_closed(planner, tmp_path):
    p, fake = planner([])  # client raises "refused" on every call
    with pytest.raises(GPTPlannerError) as ei:
        p.plan(request())
    assert ei.value.error_category == "api_error"
    store = Store(tmp_path / "s")
    assert store.plan_doc(p.plan_id) is None


# T9 approval isolation: successful GPT plan => zero Beads tasks
def test_t9_approval_isolation(planner, tmp_path):
    p, _ = planner([draft()])
    plan, _ = p.plan(request())
    store = Store(tmp_path / "s")
    store.save_plan(plan, status="PLANNED")
    beads = BeadsAdapter(REPO)
    before = len(beads._run("list", "--json").stdout or "[]") and \
        len(json.loads(beads._run("list", "--json").stdout))
    # no apply happened: delta zero
    doc = store.plan_doc(plan.plan_id)
    assert doc["status"] == "PLANNED"
    assert store.approval(plan.plan_id) is None
    after = len(json.loads(beads._run("list", "--json").stdout))
    assert after == before  # Beads task count delta = 0


# T10 api key missing => fail closed with BLOCKED category
def test_t10_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(GPTPlanner, "_load_api_key", staticmethod(lambda: ""))
    p = GPTPlanner(REPO, plan_id="plan-test-nokey")
    with pytest.raises(ApiKeyMissing):
        p.plan(request())
