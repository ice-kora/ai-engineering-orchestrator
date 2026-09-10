"""P2-01 Real Demo — "为订单模块增加运费规则及边界测试" (dependent A -> B).

Full human-request loop with a DETERMINISTIC planner (no LLM):
  Request -> Plan -> human approve (explicit gate call, approved_by=human)
  -> idempotent apply -> Beads DAG (A blocks B) -> ready queue
  -> Task-A: PullFlow claim+reserve+worktree -> implement -> self-test
     -> handover JSON -> reconcile AUTO-starts Antigravity review -> APPROVED
     -> merge + close A -> reconcile -> Task-B READY (dependency satisfied)
  -> Task-B: pull -> boundary tests + negative guard -> handover
     -> reconcile AUTO review -> APPROVED -> close B
  -> reconcile -> PLAN DONE.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.agent_mail import AgentMailAdapter  # noqa: E402
from adapters.beads import BeadsAdapter  # noqa: E402
from adapters.pull_flow import PullFlow  # noqa: E402
from adapters.review_flow import build_and_validate_handover, run_tests  # noqa: E402
from adapters.config import DEMO_REPO  # noqa: E402
from orchestrator import contracts  # noqa: E402
from orchestrator.approval import ApprovalGate  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402
from orchestrator.planner import JsonPlanner  # noqa: E402
from orchestrator.reconcile import Reconciler  # noqa: E402
from orchestrator.store import Store  # noqa: E402

repo = Path(DEMO_REPO)
store = Store(ROOT / "sandbox" / "orchestrator-state")
ev: list[str] = []

PLAN_DOC = {
    "plan_id": "{{PLAN_ID}}",
    "request_id": "{{REQUEST_ID}}",
    "target_repo": str(repo),
    "summary": "Add shipping-fee rules to the order module: core fee calculation (Task-A) "
               "and boundary/negative-input hardening (Task-B, depends on A).",
    "tasks": [
        {
            "task_key": "shipping-core",
            "title": "Implement shipping_fee(subtotal, express) core rules + happy-path tests",
            "description": "src/shipping.py implements shipping_fee(subtotal, express=False): "
                           "base fee 10 (25 when express); free shipping when subtotal >= 200.",
            "target_paths": ["src/shipping.py", "tests/test_shipping.py"],
            "acceptance_criteria": "shipping_fee(50)==10; shipping_fee(50,express=True)==25; "
                                   "shipping_fee(200)==0; shipping_fee(250,express=True)==0. "
                                   "Each rule covered by a passing unit test.",
            "dependencies": [],
            "executor_role": "zcode", "reviewer_role": "antigravity", "risk_level": "LOW",
        },
        {
            "task_key": "shipping-boundaries",
            "title": "Boundary + negative-input hardening tests for shipping",
            "description": "Add ValueError guard for negative subtotal in src/shipping.py and "
                           "boundary tests at 199.99 / 200 / 200-express, plus negative-input test.",
            "target_paths": ["src/shipping.py", "tests/test_shipping.py"],
            "acceptance_criteria": "shipping_fee(-1) raises ValueError; boundary tests cover "
                                   "199.99 (paid), 200 (free), 200 with express (free); all pass.",
            "dependencies": ["shipping-core"],
            "executor_role": "zcode", "reviewer_role": "antigravity", "risk_level": "LOW",
        },
    ],
    "risks": ["fee rules may evolve — keep constants named"],
    "assumptions": ["amounts in plain float yuan"],
    "requires_human_approval": True,
}

A_IMPL = '''"""Order module — shipping fee rules."""


def shipping_fee(subtotal: float, express: bool = False) -> float:
    """Base fee 10 (25 for express); free when subtotal >= 200."""
    base = 25.0 if express else 10.0
    if subtotal >= 200:
        return 0.0
    return base
'''

A_TESTS = '''import unittest

from src.shipping import shipping_fee


class TestShippingFee(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(shipping_fee(50), 10.0)

    def test_express(self):
        self.assertEqual(shipping_fee(50, express=True), 25.0)

    def test_free_at_200(self):
        self.assertEqual(shipping_fee(200), 0.0)

    def test_free_express_at_250(self):
        self.assertEqual(shipping_fee(250, express=True), 0.0)


if __name__ == "__main__":
    unittest.main()
'''

B_IMPL = '''"""Order module — shipping fee rules (boundary-hardened)."""


def shipping_fee(subtotal: float, express: bool = False) -> float:
    """Base fee 10 (25 for express); free when subtotal >= 200.

    Raises ValueError for negative subtotal.
    """
    if subtotal < 0:
        raise ValueError("subtotal must be non-negative")
    base = 25.0 if express else 10.0
    if subtotal >= 200:
        return 0.0
    return base
'''

B_TESTS = '''import unittest

from src.shipping import shipping_fee


class TestShippingFee(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(shipping_fee(50), 10.0)

    def test_express(self):
        self.assertEqual(shipping_fee(50, express=True), 25.0)

    def test_free_at_200(self):
        self.assertEqual(shipping_fee(200), 0.0)

    def test_free_express_at_250(self):
        self.assertEqual(shipping_fee(250, express=True), 0.0)

    def test_boundary_just_below_200(self):
        self.assertEqual(shipping_fee(199.99), 10.0)

    def test_boundary_express_200(self):
        self.assertEqual(shipping_fee(200, express=True), 0.0)

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            shipping_fee(-1)


if __name__ == "__main__":
    unittest.main()
'''


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_fresh(path: Path, content: str, task_id: str) -> None:
    """Prepend the task id so every task's commit is content-unique even if a
    previous (crashed) run already landed identical logic on main."""
    marker = "# task " + task_id + "\n"
    write(path, marker + content)


def main() -> int:
    beads = BeadsAdapter(repo)
    mail = AgentMailAdapter(repo)
    gate = ApprovalGate(store)
    recon = Reconciler(store, beads, mail)

    slug = f"shipping-{time.strftime('%H%M%S')}"
    request_id, plan_id = contracts.new_ids(slug)
    request = contracts.UserRequest(
        request_id=request_id, target_repo=str(repo),
        title="为订单模块增加运费规则及边界测试",
        description="Implement shipping fee rules (base 10 / express 25 / free>=200) "
                    "plus boundary and negative-input tests as a dependent two-task plan.",
        constraints=["sandbox only", "stdlib unittest"],
    )

    # -- Plan (deterministic JsonPlanner) -------------------------------------
    import copy
    plan_doc = copy.deepcopy(PLAN_DOC)
    plan_doc["plan_id"] = plan_id  # fill template placeholders (CLI does the same)
    plan = JsonPlanner(plan_doc).plan(request)
    plan.validate_dag()
    store.save_plan(plan, status="PLANNED")
    ev.append(f"plan {plan.plan_id} PLANNED (tasks: {[t.task_key for t in plan.tasks]})")

    # -- Human Approval Gate (explicit, approved_by=human) --------------------
    rec = gate.approve(plan.plan_id, approved_by="human")
    ev.append(f"approval: {rec['decision']} by {rec['approved_by']} at {rec['approved_at']}")

    # -- Idempotent apply ------------------------------------------------------
    report = Materializer(store, beads, gate).apply(plan)
    ev.append(f"apply: created={report.created} reused={report.reused} "
              f"deps={report.dependencies_wired} beads_count={report.beads_task_count}")
    report2 = Materializer(store, beads, gate).apply(plan)
    ev.append(f"re-apply: created={report2.created} (must be empty) count={report2.beads_task_count}")
    assert not report2.created and report2.beads_task_count == len(plan.tasks)

    a_key, b_key = "shipping-core", "shipping-boundaries"
    id_a = store.mapping(plan.plan_id)[a_key]
    id_b = store.mapping(plan.plan_id)[b_key]

    # -- DAG check: B waits, A ready ------------------------------------------
    r = recon.reconcile_plan(plan.plan_id, execute=False)
    s = {t["task_key"]: t for t in r["tasks"]}
    assert s[a_key]["state"] == "READY_FOR_PULL" and s[b_key]["state"] == "WAIT_DEPENDENCY"
    ev.append(f"DAG: {a_key}=READY_FOR_PULL, {b_key}=WAIT_DEPENDENCY")

    # -- Task-A: pull -> implement -> handover ---------------------------------
    flow = PullFlow(repo=repo)
    plan_a = flow.pull_task(task_id=id_a)
    mail.send("A", "B", id_a, f"[{id_a}] Start", "pulled via PullFlow")
    wt = plan_a.worktree
    write_fresh(wt / "src" / "shipping.py", A_IMPL, id_a)
    write_fresh(wt / "tests" / "test_shipping.py", A_TESTS, id_a)
    tests = run_tests(wt)
    assert tests["exit_code"] == 0, tests["output_summary"]
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-m", f"[{id_a}] implement shipping_fee core rules"],
                   check=True, capture_output=True)
    h = build_and_validate_handover(task_id=id_a, iteration=1, worktree=wt,
                                    branch=plan_a.branch, base=plan_a.base_commit,
                                    deliverable_summary="core fee rules (10/25/free>=200) + 4 tests",
                                    test_evidence=tests)
    store.save_handover(id_a, h)
    ev.append(f"A implemented: tests {tests['passed_tests']}/{tests['total_tests']}, handover valid")

    # -- reconcile: auto review -> APPROVED -> human close ---------------------
    r = recon.reconcile_plan(plan.plan_id)
    ev.append(f"reconcile#1 action: {r['action_executed']}")
    verdict_a = store.review(id_a)["verdict"]
    assert verdict_a == "APPROVED", store.review(id_a)
    ev.append(f"A review verdict: {verdict_a}")
    subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", plan_a.branch,
                    "-m", f"merge [{id_a}]"], check=True, capture_output=True)
    flow.finish(id_a, reservation_ids=[g["id"] for g in plan_a.reservation.granted])
    beads.close(id_a, "review APPROVED; merged", "agent-zcode")
    ev.append(f"A closed + reservation released")

    # -- reconcile: B becomes READY, notify ------------------------------------
    r = recon.reconcile_plan(plan.plan_id)
    s = {t["task_key"]: t for t in r["tasks"]}
    assert s[b_key]["state"] == "READY_FOR_PULL", s[b_key]
    ev.append(f"reconcile#2: {b_key} -> READY_FOR_PULL | action: {r['action_executed']}")

    # -- Task-B: pull -> harden -> handover -> auto review -> close -------------
    flow_b = PullFlow(repo=repo)
    plan_b = flow_b.pull_task(task_id=id_b)
    wt_b = plan_b.worktree
    write_fresh(wt_b / "src" / "shipping.py", B_IMPL, id_b)
    write_fresh(wt_b / "tests" / "test_shipping.py", B_TESTS, id_b)
    tests_b = run_tests(wt_b)
    assert tests_b["exit_code"] == 0, tests_b["output_summary"]
    subprocess.run(["git", "-C", str(wt_b), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wt_b), "commit", "-m",
                    f"[{id_b}] boundary + negative-input hardening"], check=True, capture_output=True)
    h_b = build_and_validate_handover(task_id=id_b, iteration=1, worktree=wt_b,
                                      branch=plan_b.branch, base=plan_b.base_commit,
                                      deliverable_summary="ValueError guard + boundary tests (199.99/200/200-express)",
                                      test_evidence=tests_b)
    store.save_handover(id_b, h_b)
    r = recon.reconcile_plan(plan.plan_id)
    ev.append(f"B tests {tests_b['passed_tests']}/{tests_b['total_tests']}; reconcile#3: {r['action_executed']}")
    assert store.review(id_b)["verdict"] == "APPROVED", store.review(id_b)
    subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", plan_b.branch,
                    "-m", f"merge [{id_b}]"], check=True, capture_output=True)
    flow_b.finish(id_b, reservation_ids=[g["id"] for g in plan_b.reservation.granted])
    beads.close(id_b, "review APPROVED; merged", "agent-zcode")

    # -- final reconcile: PLAN DONE --------------------------------------------
    r = recon.reconcile_plan(plan.plan_id)
    ev.append(f"final: plan_status={r['plan_status']} states="
              f"{[(t['task_key'], t['state']) for t in r['tasks']]}")
    assert r["plan_status"] == "DONE"

    print("\n== P2-01 REAL DEMO COMPLETE ==")
    for line in ev:
        print(" ", line)
    out = ROOT / "docs" / "p2-evidence"
    out.mkdir(exist_ok=True)
    (out / "p2-01-demo.txt").write_text("\n".join(ev), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
