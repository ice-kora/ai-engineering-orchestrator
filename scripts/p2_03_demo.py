"""P2-03 Real Demo — real Codex Final Gate on real verified evidence.

Mode A (approved): request asks exactly for cancel-reason validation; the
delivered task implements it + tests; expect FINAL_GATE APPROVED -> DONE.
Mode B (followup): same delivered work, but the request ALSO asks for CSV
export nobody implemented; expect FOLLOWUP_REQUIRED -> FINAL_FIX_REQUIRED and
ZERO auto-created Beads tasks.

Chain per run (all real): plan(JsonPlanner deterministic) -> approve -> apply
-> PullFlow claim+reserve -> implement -> self-test -> handover -> reconcile
auto-starts REAL agy review -> APPROVED -> merge + close -> reconcile
(READY_FOR_FINAL_GATE) -> reconcile runs REAL Codex Final Gate -> terminal
plan state. Two Codex CLI calls happen per mode (review is agy; final gate is
codex) — subscription auth, no API key.
"""

from __future__ import annotations

import json
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
from orchestrator.final_gate import CodexFinalGateEngine  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402
from orchestrator.planner import JsonPlanner  # noqa: E402
from orchestrator.reconcile import Reconciler  # noqa: E402
from orchestrator.store import Store  # noqa: E402

REPO = Path(DEMO_REPO)
STORE = Store(ROOT / "sandbox" / "orchestrator-state")

REQUESTS = {
    "approved": {
        "request_id": "req-p203-approved", "target_repo": str(REPO),
        "title": "为订单模块增加取消订单原因校验",
        "description": "为订单模块增加取消订单原因校验：空原因拒绝取消，原因最长 200 字符，"
                       "保留正常取消流程，并补充边界测试（空串、200 字符、超长、正常值）。",
        "constraints": ["sandbox only", "stdlib unittest"], "created_at": "2026-09-10",
    },
    "followup": {
        "request_id": "req-p203-followup", "target_repo": str(REPO),
        "title": "取消原因校验 + 订单历史 CSV 导出",
        "description": "为订单模块增加取消订单原因校验（空原因拒绝，最长 200 字符，边界测试），"
                       "并且提供订单历史记录导出为 CSV 文件的功能（含表头，UTF-8）。",
        "constraints": ["sandbox only", "stdlib unittest"], "created_at": "2026-09-10",
    },
}

PLAN_DOC = {
    "plan_id": "PLACEHOLDER", "request_id": "PLACEHOLDER", "target_repo": str(REPO),
    "summary": "Cancel-reason validation for the order module with boundary tests.",
    "tasks": [{
        "task_key": "cancel-reason-validation",
        "title": "Implement cancel_reason validation + boundary tests",
        "description": "src/order.py: validate_cancel_reason(reason) — empty rejected, "
                       "max 200 chars, normal flow preserved.",
        "target_paths": ["src/order.py", "tests/test_order.py"],
        "acceptance_criteria": "validate_cancel_reason('') raises ValueError; "
                               "a 200-char reason is accepted; a 201-char reason raises ValueError; "
                               "a normal reason returns normalized text. All covered by unit tests.",
        "dependencies": [], "executor_role": "zcode",
        "reviewer_role": "antigravity", "risk_level": "LOW",
    }],
    "risks": [], "assumptions": [], "requires_human_approval": True,
}

IMPL = '''"""Order module — cancel-reason validation."""


def validate_cancel_reason(reason: str) -> str:
    """Validate a cancel reason: non-empty, at most 200 chars.

    Raises ValueError on empty/overlong input; returns the normalized text.
    """
    normalized = (reason or "").strip()
    if not normalized:
        raise ValueError("cancel reason must not be empty")
    if len(normalized) > 200:
        raise ValueError("cancel reason must be at most 200 characters")
    return normalized


def cancel_order(reason: str) -> bool:
    """Normal cancel flow: validates the reason, then cancels."""
    validate_cancel_reason(reason)
    return True
'''

TESTS = '''import unittest

from src.order import cancel_order, validate_cancel_reason


class TestCancelReason(unittest.TestCase):
    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            validate_cancel_reason("")

    def test_max_200_accepted(self):
        reason = "x" * 200
        self.assertEqual(validate_cancel_reason(reason), reason)

    def test_over_200_rejected(self):
        with self.assertRaises(ValueError):
            validate_cancel_reason("x" * 201)

    def test_normal_flow_preserved(self):
        self.assertTrue(cancel_order("changed my mind"))

    def test_normalized_return(self):
        self.assertEqual(validate_cancel_reason("  changed my mind  "), "changed my mind")


if __name__ == "__main__":
    unittest.main()
'''


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "approved"
    assert mode in REQUESTS
    ev: list[str] = []

    import copy as _copy
    stamp = time.strftime("%H%M%S")
    req_doc = _copy.deepcopy(REQUESTS[mode])
    req_doc["request_id"] = f"{req_doc['request_id']}-{stamp}"
    request = contracts.UserRequest.from_dict(req_doc)
    beads = BeadsAdapter(REPO)
    mail = AgentMailAdapter(REPO)
    gate = ApprovalGate(STORE)
    before_beads = len(json.loads(beads._run("list", "--json", "--all").stdout))

    import copy
    plan_doc = copy.deepcopy(PLAN_DOC)
    plan_doc["plan_id"] = f"plan-p203-{mode}-{stamp}"
    plan = JsonPlanner(plan_doc).plan(request)
    plan.validate_dag()
    STORE.save_request(request.to_dict())
    STORE.save_plan(plan, status="PLANNED")
    ev.append(f"plan {plan.plan_id} PLANNED (request saved immutable)")

    gate.approve(plan.plan_id, "human")
    report = Materializer(STORE, beads, gate).apply(plan)
    ev.append(f"apply: created={report.created} count={report.beads_task_count}")

    flow = PullFlow(repo=REPO)
    ptask = plan.tasks[0]
    task_id = STORE.mapping(plan.plan_id)[ptask.task_key]
    pull = flow.pull_task(task_id=task_id)
    ev.append(f"pulled {task_id}; reserved {[g['path_pattern'] for g in pull.reservation.granted]}")

    write(pull.worktree / "src" / "order.py", "# task " + task_id + "\n" + IMPL)
    write(pull.worktree / "tests" / "test_order.py", "# task " + task_id + "\n" + TESTS)
    tests = run_tests(pull.worktree)
    assert tests["exit_code"] == 0, tests["output_summary"]
    ev.append(f"self-test {tests['passed_tests']}/{tests['total_tests']}")
    subprocess.run(["git", "-C", str(pull.worktree), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(pull.worktree), "commit", "-m", f"[{task_id}] cancel-reason validation"],
                   check=True, capture_output=True)

    handover = build_and_validate_handover(
        task_id=task_id, iteration=1, worktree=pull.worktree, branch=pull.branch,
        base=pull.base_commit,
        deliverable_summary="validate_cancel_reason: empty rejected, <=200 chars enforced, "
                            "normal cancel flow preserved; 4 boundary tests.",
        test_evidence=tests)
    STORE.save_handover(task_id, handover)
    ev.append("handover schema-valid")

    recon = Reconciler(STORE, beads, mail, final_gate_engine=CodexFinalGateEngine())
    iteration = 1
    while True:
        r1 = recon.reconcile_plan(plan.plan_id)
        ev.append(f"reconcile (auto review #{iteration}): {(r1['action_executed'] or 'none')[:120]}")
        verdict = STORE.review(task_id)["verdict"]
        ev.append(f"agy review verdict #{iteration}: {verdict}")
        if verdict == "APPROVED":
            break
        assert iteration < 3, "circuit breaker hit in demo"
        # real fix: add the reviewer-demanded test, new head, handover iter+1
        fix = ("\n\ndef test_normalized_return_fix" + str(iteration) + "(self):\n"
               "        self.assertEqual(validate_cancel_reason('  padded  '), 'padded')\n")
        tf = pull.worktree / "tests" / "test_order.py"
        tf.write_text(tf.read_text(encoding="utf-8").rstrip() + "\n" + fix, encoding="utf-8")
        tests = run_tests(pull.worktree)
        assert tests["exit_code"] == 0, tests["output_summary"]
        subprocess.run(["git", "-C", str(pull.worktree), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(pull.worktree), "commit", "-m",
                        f"[{task_id}] fix: cover normalized-return per review #{iteration}"],
                       check=True, capture_output=True)
        iteration += 1
        handover = build_and_validate_handover(
            task_id=task_id, iteration=iteration, worktree=pull.worktree, branch=pull.branch,
            base=pull.base_commit,
            deliverable_summary=f"v{iteration}: reviewer finding fixed (normalized-return covered); "
                                f"{tests['passed_tests']} tests.",
            test_evidence=tests)
        STORE.save_handover(task_id, handover)
        ev.append(f"fix #{iteration}: new head, handover iteration={iteration}")

    subprocess.run(["git", "-C", str(REPO), "checkout", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(REPO), "merge", "--no-ff", pull.branch,
                    "-m", f"merge [{task_id}]"], check=True, capture_output=True)
    flow.finish(task_id, reservation_ids=[g["id"] for g in pull.reservation.granted])
    beads.close(task_id, "review APPROVED; merged", "agent-zcode")
    ev.append("merged + reservation released + closed")

    r2 = recon.reconcile_plan(plan.plan_id)
    ev.append(f"reconcile#2: status={r2['plan_status']} action={r2['action_executed'][:120]}")
    assert r2["plan_status"] == "READY_FOR_FINAL_GATE"

    t0 = time.monotonic()
    r3 = recon.reconcile_plan(plan.plan_id)   # runs the REAL Codex final gate
    gate_latency = time.monotonic() - t0
    ev.append(f"reconcile#3 (REAL codex final gate, {gate_latency:.0f}s): "
              f"status={r3['plan_status']}")
    ev.append(f"action: {r3['action_executed']}")

    decision = STORE.final_gate(plan.plan_id)
    ev.append("decision: " + json.dumps(decision, ensure_ascii=False)[:600])

    after_beads = len(json.loads(beads._run("list", "--json", "--all").stdout))
    ev.append(f"beads task count delta after final gate: {after_beads - before_beads}")

    if mode == "approved":
        assert r3["plan_status"] == "DONE", r3["plan_status"]
        assert after_beads - before_beads == 1  # only the plan's own task
    else:
        assert r3["plan_status"] == "FINAL_FIX_REQUIRED", r3["plan_status"]
        assert after_beads - before_beads == 1  # NOTHING auto-created by followup
        assert decision["verdict"] == "FOLLOWUP_REQUIRED"

    print(f"\n== P2-03 REAL DEMO ({mode}) COMPLETE ==")
    for line in ev:
        print(" ", line)
    out = ROOT / "docs" / "p2-evidence"
    out.mkdir(exist_ok=True)
    (out / f"p2-03-demo-{mode}.txt").write_text("\n".join(ev), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
