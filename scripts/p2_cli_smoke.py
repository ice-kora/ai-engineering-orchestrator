"""P2-03 Hotfix REAL CLI Final Gate Smoke (small, gated on codex login).

Proves the CLI/composition path (not just p2_03_demo.py) can drive a plan to
READY_FOR_FINAL_GATE and then execute the REAL Codex Final Gate via
`orchestrate reconcile`. No implementation round is re-run: the task is driven
to mechanically-DONE through the verified store/materializer/review chain in
compact form, then the last two CLI calls do the semantic work.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import handover as hm  # noqa: E402
from adapters.beads import BeadsAdapter  # noqa: E402
from orchestrator import contracts  # noqa: E402
from orchestrator.approval import ApprovalGate  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402
from orchestrator.store import Store  # noqa: E402

REPO = (ROOT / "sandbox" / "demo-repo").resolve()
STORE = Store(ROOT / "sandbox" / "orchestrator-state")


def cli(*args: str) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ, UV_DEFAULT_INDEX="https://pypi.org/simple")
    return subprocess.run(["uv", "run", "python", str(ROOT / "scripts" / "orchestrate.py"), *args],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600, env=env)


def main() -> int:
    marker = uuid.uuid4().hex[:6]
    request = contracts.UserRequest(
        request_id=f"req-clismoke-{marker}", target_repo=str(REPO),
        title="CLI smoke: order cancel reason length cap",
        description="取消订单原因校验：空原因拒绝，最长 200 字符，补充边界测试。",
        constraints=["sandbox only"])
    plan = contracts.ExecutionPlan(
        plan_id=f"plan-clismoke-{marker}", request_id=request.request_id,
        target_repo=str(REPO), summary="CLI final-gate smoke",
        tasks=[contracts.PlannedTask(
            task_key=f"clismoke-{marker}", title=f"cli smoke {marker}",
            description="d", target_paths=[f"src/cs-{marker}.py"],
            acceptance_criteria="criterion met")])
    plan.validate_dag()
    STORE.save_request(request.to_dict())
    STORE.save_plan(plan, status="PLANNED")
    beads = BeadsAdapter(REPO)
    gate = ApprovalGate(STORE)
    gate.approve(plan.plan_id, "human")
    Materializer(STORE, beads, gate).apply(plan)
    ptask = plan.tasks[0]
    tid = STORE.mapping(plan.plan_id)[ptask.task_key]
    beads.claim(tid, "agent-zcode")

    wt = REPO / "worktrees" / "agent-zcode"
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", str(wt),
                    "-b", f"agent/zcode/{tid}", "main"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(wt), "checkout", "-B", f"agent/zcode/{tid}", "main"],
                   capture_output=True, check=True)
    f = wt / ptask.target_paths[0]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# {tid}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-m", f"[{tid}] cli smoke"],
                   capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    base = subprocess.run(["git", "-C", str(REPO), "rev-parse", "main"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    h = hm.build_handover(task_id=tid, iteration=1, worktree_path=str(wt),
                          branch_name=f"agent/zcode/{tid}", base_commit=base,
                          head_commit=head, affected_files=[ptask.target_paths[0]],
                          test_command="python -m unittest", test_exit_code=0,
                          total_tests=1, passed_tests=1, failed_tests=0,
                          output_summary="OK",
                          deliverable_summary="cancel-reason validation: empty reason rejected (ValueError), max 200 chars enforced, normal flow preserved; boundary tests cover empty/200/201/normal cases (all passing).")
    STORE.save_handover(tid, h)
    STORE.save_review(tid, {"schema_version": "1.1.0", "task_id": tid, "iteration": 1,
                            "reviewer": "agent-antigravity", "verdict": "APPROVED",
                            "verified_head_commit": head[:12], "findings": []})
    subprocess.run(["git", "-C", str(REPO), "checkout", "main"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(REPO), "merge", "--no-ff", f"agent/zcode/{tid}",
                    "-m", f"merge [{tid}]"], capture_output=True, check=True)
    beads.close(tid, "smoke done", "test")

    # === the part under test: the REAL CLI path ===
    p1 = cli("reconcile", plan.plan_id)                 # -> READY_FOR_FINAL_GATE
    assert "READY_FOR_FINAL_GATE" in p1.stdout, p1.stdout[-500:]
    t0 = time.monotonic()
    p2 = cli("reconcile", plan.plan_id)                 # -> REAL codex final gate
    latency = time.monotonic() - t0
    assert p2.returncode == 0, p2.stdout[-500:] + p2.stderr[-500:]
    status = STORE.plan_status(plan.plan_id)
    decision = STORE.final_gate(plan.plan_id)

    print(json.dumps({
        "REAL_CLI_FINAL_GATE_SMOKE": "SUCCESS" if status == "DONE" else "CHECK",
        "plan_id": plan.plan_id, "plan_status": status,
        "codex_gate_latency_s": round(latency, 1),
        "verdict": decision.get("verdict"), "summary": decision.get("summary", "")[:120],
        "coverage": [{c["requirement"][:40]: c["status"]}
                     for c in decision.get("request_coverage", [])],
    }, ensure_ascii=False, indent=1))
    return 0 if status == "DONE" else 5


if __name__ == "__main__":
    raise SystemExit(main())
