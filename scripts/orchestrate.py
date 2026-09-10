"""P2-01.10 — minimal orchestrator CLI.

  plan <request.json> [--planner <plan.json>]   deterministic plan (JsonPlanner)
  show <plan_id>                                plan doc + status
  approve <plan_id> [--by human]                explicit human approval (gate)
  reject <plan_id> [--by human]
  apply <plan_id>                               idempotent DAG materialization
  status <plan_id>                              live task states (dry reconcile)
  reconcile <plan_id> [--dry-run]               one-step advancement
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.agent_mail import AgentMailAdapter  # noqa: E402
from adapters.beads import BeadsAdapter  # noqa: E402
from adapters.config import DEMO_REPO  # noqa: E402
from orchestrator import contracts  # noqa: E402
from orchestrator.approval import ApprovalGate  # noqa: E402
from orchestrator.materialize import Materializer  # noqa: E402
from orchestrator.planner import JsonPlanner  # noqa: E402
from orchestrator.reconcile import Reconciler  # noqa: E402
from orchestrator.store import Store  # noqa: E402

STORE_ROOT = ROOT / "sandbox" / "orchestrator-state"


def _repo_path(target: str) -> Path:
    p = Path(target)
    return p if p.is_absolute() else (ROOT / p).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(prog="orchestrate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan"); p_plan.add_argument("request"); p_plan.add_argument("--planner", required=True)
    p_show = sub.add_parser("show"); p_show.add_argument("plan_id")
    p_app = sub.add_parser("approve"); p_app.add_argument("plan_id"); p_app.add_argument("--by", default="human")
    p_rej = sub.add_parser("reject"); p_rej.add_argument("plan_id"); p_rej.add_argument("--by", default="human")
    p_apply = sub.add_parser("apply"); p_apply.add_argument("plan_id")
    p_status = sub.add_parser("status"); p_status.add_argument("plan_id")
    p_rec = sub.add_parser("reconcile"); p_rec.add_argument("plan_id"); p_rec.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = Store(STORE_ROOT)

    if args.cmd == "plan":
        request = contracts.UserRequest.from_dict(contracts.load_json(args.request))
        plan_doc = contracts.load_json(args.planner)
        if "{{" in json.dumps(plan_doc):  # template auto-ids
            slug = request.request_id.removeprefix("req-")[:20]
            plan_doc["plan_id"] = f"plan-{slug}"
        planner = JsonPlanner(plan_doc)
        plan = planner.plan(request)
        plan.validate_dag()
        store.save_plan(plan, status="PLANNED")
        print(json.dumps({"plan_id": plan.plan_id, "status": "PLANNED",
                          "tasks": [t.task_key for t in plan.tasks],
                          "requires_human_approval": plan.requires_human_approval},
                         ensure_ascii=False, indent=1))
        print("NEXT: orchestrate show / approve (explicit human action)")
        return 0

    if args.cmd == "show":
        doc = store.plan_doc(args.plan_id)
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0

    if args.cmd in ("approve", "reject"):
        gate = ApprovalGate(store)
        rec = gate.approve(args.plan_id, args.by) if args.cmd == "approve" \
            else gate.reject(args.plan_id, args.by)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "apply":
        plan = store.plan(args.plan_id)
        beads = BeadsAdapter(_repo_path(plan.target_repo))
        report = Materializer(store, beads, ApprovalGate(store)).apply(plan)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=1))
        return 0

    if args.cmd in ("status", "reconcile"):
        plan = store.plan(args.plan_id)
        beads = BeadsAdapter(_repo_path(plan.target_repo))
        mail = AgentMailAdapter(_repo_path(plan.target_repo))
        result = Reconciler(store, beads, mail).reconcile_plan(
            args.plan_id, execute=(args.cmd == "reconcile" and not args.dry_run))
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
