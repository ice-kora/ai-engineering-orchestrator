"""P2-02 Real API Smoke (gated on OPENAI_API_KEY; sandbox repo only).

Requirement (per GPT instruction):
  为订单模块增加取消订单原因校验。要求空原因拒绝，原因最长 200 字符，
  保留正常取消流程，并补充边界测试。

Success criteria (stop after PLANNED — no ZCode implementation round):
  GPTPlanner -> local validation PASS -> save PLANNED
  -> Beads task count delta == 0, approval record NONE, status PLANNED.
No key -> BLOCKED_API_KEY_MISSING (fail closed, nothing saved).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.beads import BeadsAdapter  # noqa: E402
from orchestrator import contracts  # noqa: E402
from orchestrator.gpt_planner import ApiKeyMissing, GPTPlanner, GPTPlannerError  # noqa: E402
from orchestrator.store import Store  # noqa: E402

REPO = (ROOT / "sandbox" / "demo-repo").resolve()
STORE = Store(ROOT / "sandbox" / "orchestrator-state")
EVIDENCE = ROOT / "sandbox" / "planner-evidence"


def beads_count() -> int:
    out = subprocess.run([r"D:\Software\ai-orchestrator\beads\bd.exe", "list", "--json"],
                         cwd=str(REPO), capture_output=True, text=True, encoding="utf-8").stdout
    return len(json.loads(out or "[]"))


def main() -> int:
    request = contracts.UserRequest(
        request_id="req-smoke-cancel-reason", target_repo=str(REPO),
        title="为订单模块增加取消订单原因校验",
        description="为订单模块增加取消订单原因校验：空原因拒绝，原因最长 200 字符，"
                    "保留正常取消流程，并补充边界测试。",
        constraints=["sandbox only", "stdlib unittest"],
    )
    planner = GPTPlanner(REPO, plan_id="plan-smoke-cancel-reason", evidence_dir=EVIDENCE)

    before = beads_count()
    try:
        plan, ev = planner.plan(request)
    except ApiKeyMissing:
        print("REAL_API_SMOKE = BLOCKED_API_KEY_MISSING")
        print("(fill OPENAI_API_KEY in .env and re-run; nothing was saved or requested)")
        return 3
    except GPTPlannerError as exc:
        print(f"REAL_API_SMOKE = FAILED ({exc.error_category})")
        print(json.dumps(exc.evidence, ensure_ascii=False, indent=1))
        return 4

    # save LAST (atomicity) — only after every gate passed
    STORE.save_plan(plan, status="PLANNED")

    after = beads_count()
    approval = STORE.approval(plan.plan_id)
    status = STORE.plan_status(plan.plan_id)
    result = {
        "REAL_API_SMOKE": "SUCCESS",
        "plan_id": plan.plan_id,
        "plan_status": status,                     # expect PLANNED
        "beads_task_count_delta": after - before,  # expect 0
        "approval_record": approval,               # expect None
        "tasks": [{"task_key": t.task_key, "deps": t.dependencies,
                   "paths": t.target_paths} for t in plan.tasks],
        "model_meta": {k: ev.summary()[k] for k in ("model", "reasoning_effort", "prompt_version")},
        "attempts": ev.summary()["attempts"],
        "next": f"orchestrate show {plan.plan_id}",
    }
    assert status == "PLANNED" and after - before == 0 and approval is None
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
