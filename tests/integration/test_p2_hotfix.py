"""P2-01 hotfix state-integrity tests (real components; unique plan per test).

Fix-1 Beads-authority recovery | Fix-2 review freshness | Fix-3 closed invariant.
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
from orchestrator.reconcile import Reconciler, State  # noqa: E402
from orchestrator.store import Store  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"
WT = REPO / "worktrees" / "agent-zcode"

pytestmark = pytest.mark.skipif(not (REPO / ".beads").exists(), reason="demo-repo not initialized")


def make_plan(store: Store, n_tasks: int = 1) -> contracts.ExecutionPlan:
    marker = uuid.uuid4().hex[:6]
    tasks = [
        contracts.PlannedTask(
            task_key=f"hf{i}-{marker}", title=f"hotfix probe {i} {marker}",
            description="probe", target_paths=[f"src/hf{i}-{marker}.py"],
            acceptance_criteria="probe criterion",
            dependencies=[f"hf{i-1}-{marker}"] if i else [])
        for i in range(n_tasks)
    ]
    plan = contracts.ExecutionPlan(
        plan_id=f"plan-hf{marker}", request_id=f"req-hf{marker}", target_repo=str(REPO),
        summary="hotfix probes", tasks=tasks)
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
    branches: list[str] = []
    try:
        yield store, beads, mail, gate, created, branches
    finally:
        BeadsAdapter.create = orig_create
        for tid in created:
            beads._run("close", tid, "--reason", "hotfix test cleanup", check=False)
        for r in mail.active_reservations():
            mail._am("file_reservations", "release", mail.project_key, r["agent"], check=False)
        subprocess.run(["git", "-C", str(REPO), "checkout", "main"],
                       capture_output=True, cwd=str(REPO))
        for b in branches:
            subprocess.run(["git", "-C", str(REPO), "branch", "-D", b], capture_output=True)


def applied(env, plan):
    store, beads, mail, gate, *_ = env
    gate.approve(plan.plan_id, "human")
    return Materializer(store, beads, gate).apply(plan)


def real_commit(env, task_id: str, marker: str) -> tuple[str, str]:
    """Create a real commit on agent/zcode/<task_id>; returns (branch, full head)."""
    subprocess.run(["git", "-C", str(REPO), "worktree", "add",
                    str(WT), "-b", f"agent/zcode/{task_id}", "main"],
                   capture_output=True, check=False)
    subprocess.run(["git", "-C", str(WT), "checkout", "-B", f"agent/zcode/{task_id}", "main"],
                   capture_output=True, check=True)
    f = WT / f"src/hf-{marker}.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# {marker}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(WT), "add", "-A"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(WT), "commit", "-m", f"[{task_id}] {marker}"],
                   capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(WT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    env[5].append(f"agent/zcode/{task_id}")
    return f"agent/zcode/{task_id}", head


def handover_payload(task_id: str, iteration: int, branch: str, head: str, base: str | None = None) -> dict:
    base = base or ("0" * 40)
    payload = hm.build_handover(
        task_id=task_id, iteration=iteration, worktree_path=str(WT),
        branch_name=branch, base_commit=base if len(base) >= 7 and set(base) != {"0"} else "0" * 40,
        head_commit=head, affected_files=["src/x.py"],
        test_command="t", test_exit_code=0, total_tests=1, passed_tests=1,
        failed_tests=0, output_summary="OK", deliverable_summary="d")
    assert hm.validate_payload(payload, "handover") == []
    return payload


def review_payload(task_id: str, iteration: int, head: str, verdict: str = "APPROVED") -> dict:
    return {
        "schema_version": "1.1.0", "task_id": task_id, "iteration": iteration,
        "reviewer": "agent-antigravity", "verdict": verdict,
        "verified_head_commit": head[:12], "findings": [],
    }


# ================= Fix-1: Beads-authority reconciliation =================

def test_stale_ledger_recovery(env):
    plan = make_plan(env[0], n_tasks=1)
    _ = applied(env, plan)
    store, beads, *_ = env
    key = plan.tasks[0].task_key
    live_id = store.mapping(plan.plan_id)[key]
    store._write(f"mapping/{plan.plan_id}.json", {key: "demo-repo-zzzz"})  # stale pointer
    r = Materializer(store, beads, env[3]).apply(plan)
    assert r.created == [] and r.beads_task_count == 1
    assert store.mapping(plan.plan_id)[key] == live_id  # ledger repaired to live


def test_live_beads_overrides_wrong_ledger(env):
    plan = make_plan(env[0], n_tasks=2)
    _ = applied(env, plan)
    store, beads, *_ = env
    k0, k1 = [t.task_key for t in plan.tasks]
    live0 = store.mapping(plan.plan_id)[k0]
    live1 = store.mapping(plan.plan_id)[k1]
    store._write(f"mapping/{plan.plan_id}.json", {k0: live1, k1: live0})  # crossed pointers
    r = Materializer(store, beads, env[3]).apply(plan)
    assert r.created == [] and r.beads_task_count == 2
    mapping = store.mapping(plan.plan_id)
    assert mapping[k0] == live0 and mapping[k1] == live1  # LIVE wins, no swaps


def test_duplicate_live_taskkey_blocks(env):
    plan = make_plan(env[0], n_tasks=1)
    _ = applied(env, plan)
    store, beads, *_ = env
    key = plan.tasks[0].task_key
    # simulate a duplicated live task carrying the same TaskKey/Plan markers
    dup = beads.create("dup probe", priority=2,
                       description=f"TaskKey: {key}\nPlan: {plan.plan_id}\nPaths: src/dup.py\nAcceptance: x")
    env[4].append(dup)
    with pytest.raises(Materializer.AmbiguousTaskKey):
        Materializer(store, beads, env[3]).apply(plan)


# ================= Fix-2: review freshness =================

def test_changes_requested_then_new_handover_reopens_review(env):
    store, beads, mail, gate, *_ = env
    plan = make_plan(store, n_tasks=1)
    applied(env, plan)
    recon = Reconciler(store, beads, mail)
    tid = store.mapping(plan.plan_id)[plan.tasks[0].task_key]
    beads.claim(tid, "agent-zcode")

    branch, head1 = real_commit(env, tid, f"c1-{plan.plan_id}")
    h1 = handover_payload(tid, 1, branch, head1)
    store.save_handover(tid, h1)
    store.save_review(tid, review_payload(tid, 1, head1, "CHANGES_REQUESTED"))
    r = recon.reconcile_plan(plan.plan_id, execute=False)
    assert r["tasks"][0]["state"] == State.FIX_REQUIRED.value

    branch, head2 = real_commit(env, tid, f"c2-{plan.plan_id}")  # fix lands new head
    h2 = handover_payload(tid, 2, branch, head2)
    store.save_handover(tid, h2)
    r = recon.reconcile_plan(plan.plan_id, execute=False)
    st = r["tasks"][0]
    assert st["state"] == State.READY_FOR_REVIEW.value
    assert st["next_action"] == "START_REVIEW"
    assert st["review_stale"] is True and st["stale_review_verdict"] == "CHANGES_REQUESTED"

    # execute: review #2 must actually start against handover #2
    r = recon.reconcile_plan(plan.plan_id, execute=True)
    assert "START_REVIEW" in r["action_executed"]
    fresh = store.review(tid)
    assert fresh["iteration"] == 2  # machine check: the new review belongs to h2


def test_stale_approved_review_cannot_close_new_head(env):
    store, beads, mail, gate, *_ = env
    plan = make_plan(store, n_tasks=1)
    applied(env, plan)
    recon = Reconciler(store, beads, mail)
    tid = store.mapping(plan.plan_id)[plan.tasks[0].task_key]
    beads.claim(tid, "agent-zcode")

    branch, head1 = real_commit(env, tid, f"s1-{plan.plan_id}")
    store.save_handover(tid, handover_payload(tid, 1, branch, head1))
    store.save_review(tid, review_payload(tid, 1, head1, "APPROVED"))
    r = recon.reconcile_plan(plan.plan_id, execute=False)
    assert r["tasks"][0]["state"] == State.READY_TO_CLOSE.value  # fresh at this point

    branch, head2 = real_commit(env, tid, f"s2-{plan.plan_id}")  # new work invalidates it
    store.save_handover(tid, handover_payload(tid, 2, branch, head2))
    r = recon.reconcile_plan(plan.plan_id, execute=False)
    st = r["tasks"][0]
    assert st["state"] != State.READY_TO_CLOSE.value
    assert st["state"] != State.DONE.value
    assert st["state"] == State.READY_FOR_REVIEW.value  # old APPROVED reopens review


# ================= Fix-3: closed completion invariant =================

def _closed_with(env, *, handover=None, review=None, merge_head: str | None = None,
                 hold_lease: bool = False):
    store, beads, mail, gate, *_ = env
    plan = make_plan(store, n_tasks=1)
    applied(env, plan)
    tid = store.mapping(plan.plan_id)[plan.tasks[0].task_key]
    if handover:
        store.save_handover(tid, handover)
    if review:
        store.save_review(tid, review)
    if merge_head:
        subprocess.run(["git", "-C", str(REPO), "checkout", "main"],
                       capture_output=True, check=True)
        subprocess.run(["git", "-C", str(REPO), "merge", "--no-ff", handover["git_context"]["branch_name"],
                        "-m", f"merge [{tid}]"], capture_output=True, check=True)
    if hold_lease:
        res = mail.reserve("A", [f"src/lease-{plan.plan_id}.py"], reason=tid, ttl_seconds=120)
        assert res.success
    beads.close(tid, "test close", "test")
    recon = Reconciler(store, beads, mail)
    return plan, tid, recon.reconcile_plan(plan.plan_id, execute=False)


def test_closed_without_review_not_done(env):
    plan, tid, r = _closed_with(env)  # no handover, no review
    assert r["tasks"][0]["state"] == State.INCONSISTENT_CLOSED.value
    assert "no handover" in ";".join(r["tasks"][0]["failed_invariants"])
    assert r["plan_status"] != "DONE"


def test_closed_with_stale_review_not_done(env):
    store = env[0]
    branch, head1 = real_commit(env, "probe-stale", f"x1-{uuid.uuid4().hex[:6]}")
    branch2, head2 = real_commit(env, "probe-stale2", f"x2-{uuid.uuid4().hex[:6]}")
    h = handover_payload("demo-repo-xxxx", 2, branch2, head2)
    plan, tid, r = _closed_with(env, handover=h, review=review_payload(h["task_id"], 1, head1, "APPROVED"))
    assert r["tasks"][0]["state"] == State.INCONSISTENT_CLOSED.value
    assert any("not current" in f for f in r["tasks"][0]["failed_invariants"])


def test_closed_unmerged_head_not_done(env):
    branch, head = real_commit(env, "probe-um", f"u1-{uuid.uuid4().hex[:6]}")
    h = handover_payload("demo-repo-yyyy", 1, branch, head)
    plan, tid, r = _closed_with(env, handover=h, review=review_payload(h["task_id"], 1, head, "APPROVED"))
    assert r["tasks"][0]["state"] == State.INCONSISTENT_CLOSED.value
    assert any("not merged" in f for f in r["tasks"][0]["failed_invariants"])


def test_closed_with_live_lease_not_done(env):
    branch, head = real_commit(env, "probe-lease", f"l1-{uuid.uuid4().hex[:6]}")
    h = handover_payload("demo-repo-zzzz", 1, branch, head)
    plan, tid, r = _closed_with(env, handover=h, review=review_payload(h["task_id"], 1, head),
                                merge_head=head, hold_lease=True)
    assert r["tasks"][0]["state"] == State.INCONSISTENT_CLOSED.value
    assert any("reservation" in f for f in r["tasks"][0]["failed_invariants"])


def test_fully_verified_closed_task_done(env):
    branch, head = real_commit(env, "probe-ok", f"o1-{uuid.uuid4().hex[:6]}")
    h = handover_payload("demo-repo-okok", 1, branch, head)
    plan, tid, r = _closed_with(env, handover=h, review=review_payload(h["task_id"], 1, head),
                                merge_head=head, hold_lease=False)
    # P2-03 SS4: task-level DONE still requires the full completion invariant
    assert r["tasks"][0]["state"] == State.DONE.value
    # ...but the PLAN no longer jumps to DONE: the final-gate lifecycle owns
    # that (APPLIED -> READY_FOR_FINAL_GATE -> gate -> DONE). This legacy probe
    # stores no UserRequest, so the gate is deferred and the plan stays APPLIED.
    assert r["plan_status"] in ("APPLIED", "READY_FOR_FINAL_GATE")
    assert r["plan_status"] != "DONE"
