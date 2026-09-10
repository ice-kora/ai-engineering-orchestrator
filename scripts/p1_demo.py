"""P1-05: the real minimal collaboration loop.

Scenario (slightly more real than calculator, zero production surface):
  ZCode pulls the `shopping cart` task whose acceptance REQUIRES a volume
  discount. v1 deliberately ships without it and without a test for it —
  the independent Antigravity review must catch this (CHANGES_REQUESTED),
  ZCode fixes + adds the test, re-review must APPROVE, then release+close.

Unified task id must appear in: Beads, mail thread, reservation reason,
branch, commits, handover, review reports.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.agent_mail import AgentMailAdapter  # noqa: E402
from adapters.beads import BeadsAdapter  # noqa: E402
from adapters.pull_flow import PullFlow, SagaCompensated  # noqa: E402
from adapters.review_flow import build_and_validate_handover, review_iteration, run_tests  # noqa: E402
from adapters.config import DEMO_REPO  # noqa: E402

TASK_TITLE = "P1 loop: ShoppingCart with volume discount + tests"

DESCRIPTION = """Paths: src/shopping_cart.py, tests/test_shopping_cart.py
Acceptance: ShoppingCart.add(item, price) accumulates items; total() returns the subtotal, and applies a 10% discount when the subtotal is >= 100 (discounted total = subtotal * 0.9). Both behaviours must be covered by unit tests that pass.
"""

V1_IMPL = '''"""ShoppingCart — P1 collaboration-loop deliverable."""


class ShoppingCart:
    def __init__(self) -> None:
        self._items: list[tuple[str, float]] = []

    def add(self, item: str, price: float) -> None:
        self._items.append((item, price))

    def total(self) -> float:
        return sum(price for _, price in self._items)
'''

V1_TESTS = '''import unittest

from src.shopping_cart import ShoppingCart


class TestShoppingCart(unittest.TestCase):
    def test_add_accumulates(self):
        cart = ShoppingCart()
        cart.add("book", 30.0)
        cart.add("pen", 5.0)
        self.assertEqual(cart.total(), 35.0)


if __name__ == "__main__":
    unittest.main()
'''

FIX_IMPL_DELTA = (
    "FIX v2: apply 10% discount when subtotal >= 100 (acceptance criterion)."
)

V2_IMPL = '''"""ShoppingCart — P1 collaboration-loop deliverable (v2: volume discount)."""


class ShoppingCart:
    DISCOUNT_THRESHOLD = 100.0
    DISCOUNT_RATE = 0.10

    def __init__(self) -> None:
        self._items: list[tuple[str, float]] = []

    def add(self, item: str, price: float) -> None:
        self._items.append((item, price))

    def total(self) -> float:
        subtotal = sum(price for _, price in self._items)
        if subtotal >= self.DISCOUNT_THRESHOLD:
            return round(subtotal * (1 - self.DISCOUNT_RATE), 2)
        return subtotal
'''

V2_TESTS = '''import unittest

from src.shopping_cart import ShoppingCart


class TestShoppingCart(unittest.TestCase):
    def test_add_accumulates(self):
        cart = ShoppingCart()
        cart.add("book", 30.0)
        cart.add("pen", 5.0)
        self.assertEqual(cart.total(), 35.0)

    def test_volume_discount_applied_at_100(self):
        cart = ShoppingCart()
        cart.add("keyboard", 100.0)
        self.assertEqual(cart.total(), 90.0)

    def test_volume_discount_not_applied_below_100(self):
        cart = ShoppingCart()
        cart.add("mouse", 99.99)
        self.assertEqual(cart.total(), 99.99)


if __name__ == "__main__":
    unittest.main()
'''


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    repo = Path(DEMO_REPO)
    beads = BeadsAdapter(repo)
    mail = AgentMailAdapter(repo)
    evidence: list[str] = []

    # -- 1) create task ------------------------------------------------------
    task_id = beads.create(TASK_TITLE, priority=1, description=DESCRIPTION)
    evidence.append(f"beads create -> {task_id}")

    # -- 2) pull (claim + reservation saga + worktree) -----------------------
    flow = PullFlow(repo=repo)
    try:
        plan = flow.pull_task(task_id=task_id)
    except SagaCompensated as exc:
        print(f"UNEXPECTED compensation in demo: {exc}")
        return 2
    evidence.extend(plan.evidence)
    mail.send("A", "B", task_id, f"[{task_id}] Start: {TASK_TITLE}",
              "claimed + reserved; implementation begins.")
    evidence.append(f"mail send Start thread={task_id}")

    wt, branch, base = plan.worktree, plan.branch, plan.base_commit

    # -- 3) implementation v1 (deliberately missing the discount) ------------
    write(wt / "src" / "shopping_cart.py", V1_IMPL)
    write(wt / "tests" / "test_shopping_cart.py", V1_TESTS)
    tests_v1 = run_tests(wt)
    if tests_v1["exit_code"] != 0:
        print("v1 self-test unexpectedly failed — aborting");
        return 3
    evidence.append(f"v1 self-test: {tests_v1['passed_tests']}/{tests_v1['total_tests']} passed")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-m", f"[{task_id}] implement ShoppingCart (v1)"],
                   check=True, capture_output=True)

    # -- 4) handover #1 + independent review (expect CHANGES_REQUESTED) ------
    handover_1 = build_and_validate_handover(
        task_id=task_id, iteration=1, worktree=wt, branch=branch, base=base,
        deliverable_summary="ShoppingCart v1: add()/total() with accumulation. "
                            "NOTE: volume discount intentionally deferred.",
        known_risks=["discount behaviour not yet implemented"],
        test_evidence=tests_v1)
    evidence.append(f"handover#1 schema-valid (head={handover_1['git_context']['head_commit'][:9]})")
    report_1 = review_iteration(repo=repo, task=plan.task.__dict__, acceptance=plan.task.acceptance_criteria(),
                                handover_payload=handover_1)
    evidence.append(f"review#1 verdict={report_1['verdict']} findings={len(report_1['findings'])} "
                    f"worktree_clean={report_1.get('_worktree_clean_after_review')}")
    print(f"review#1 -> {report_1['verdict']}")
    if report_1["verdict"] != "CHANGES_REQUESTED":
        print("SCENARIO INTEGRITY FAIL: reviewer approved v1 despite missing acceptance behaviour")
        print(json.dumps(report_1, ensure_ascii=False, indent=1))
        return 4

    beads.add_label(task_id, "stage:REVIEW", "agent-zcode")

    # -- 5) fix (real) --------------------------------------------------------
    write(wt / "src" / "shopping_cart.py", V2_IMPL)
    write(wt / "tests" / "test_shopping_cart.py", V2_TESTS)
    tests_v2 = run_tests(wt)
    if tests_v2["exit_code"] != 0 or tests_v2["failed_tests"]:
        print("v2 self-test failed — aborting")
        return 3
    evidence.append(f"fix self-test: {tests_v2['passed_tests']}/{tests_v2['total_tests']} passed")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-m",
                    f"[{task_id}] {FIX_IMPL_DELTA}"], check=True, capture_output=True)

    # -- 6) handover #2 + re-review (expect APPROVED) -------------------------
    handover_2 = build_and_validate_handover(
        task_id=task_id, iteration=2, worktree=wt, branch=branch, base=base,
        deliverable_summary="v2: 10% volume discount at subtotal>=100 implemented + 3 tests "
                            "(incl. boundary at 100 and below-100).",
        test_evidence=tests_v2)
    evidence.append(f"handover#2 schema-valid (head={handover_2['git_context']['head_commit'][:9]})")
    report_2 = review_iteration(repo=repo, task=plan.task.__dict__, acceptance=plan.task.acceptance_criteria(),
                                handover_payload=handover_2)
    evidence.append(f"review#2 verdict={report_2['verdict']} findings={len(report_2['findings'])} "
                    f"worktree_clean={report_2.get('_worktree_clean_after_review')}")
    print(f"review#2 -> {report_2['verdict']}")
    if report_2["verdict"] != "APPROVED":
        print("re-review did not approve — loop would continue (circuit breaker at 3)")
        return 5

    # -- 7) merge + release + close ------------------------------------------
    subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", branch,
                    "-m", f"merge [{task_id}] into main (review APPROVED)"],
                   check=True, capture_output=True)
    evidence.append(f"merged {branch} -> main")
    flow.finish(task_id)
    evidence.append("reservation released")
    mail.send("B", "A", task_id, f"[{task_id}] VERIFIED + merged",
              "cross review APPROVED; merged to main; reservation released.")
    beads.close(task_id, "P1 loop complete: handover x2, review CHANGES_REQUESTED->APPROVED, merged", "agent-zcode")
    evidence.append(f"beads close {task_id}")

    # -- 8) unified-id proof --------------------------------------------------
    print("\n== P1 LOOP COMPLETE ==")
    for line in evidence:
        print(" ", line)
    out = {
        "task_id": task_id,
        "branch": branch,
        "commits": f"git -C {repo} log --oneline --grep [{task_id}]",
        "thread": task_id,
        "reservation_reason": task_id,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    (Path(__file__).resolve().parents[1] / "docs" / "p1-evidence").mkdir(exist_ok=True)
    (Path(__file__).resolve().parents[1] / "docs" / "p1-evidence" / "p1-05-loop.txt").write_text(
        "\n".join(evidence), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
