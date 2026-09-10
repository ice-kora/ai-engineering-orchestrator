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

import os as _os
_OVERRIDE = _os.environ.get("STORE_ROOT_OVERRIDE")
STORE_ROOT = Path(_OVERRIDE) if _OVERRIDE else (ROOT / "sandbox" / "orchestrator-state")


def _repo_path(target: str) -> Path:
    p = Path(target)
    return p if p.is_absolute() else (ROOT / p).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(prog="orchestrate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan"); p_plan.add_argument("request")
    p_plan.add_argument("--planner", default="json", choices=["json", "gpt", "codex"])
    p_plan.add_argument("--plan-doc", default=None,
                        help="json mode: path to the deterministic plan document (required for --planner json)")
    p_plan.add_argument("--plan-id", default=None, help="gpt: preallocated stable plan id (retries reuse it)")
    p_show = sub.add_parser("show"); p_show.add_argument("plan_id")
    p_app = sub.add_parser("approve"); p_app.add_argument("plan_id"); p_app.add_argument("--by", default="human")
    p_rej = sub.add_parser("reject"); p_rej.add_argument("plan_id"); p_rej.add_argument("--by", default="human")
    p_apply = sub.add_parser("apply"); p_apply.add_argument("plan_id")
    p_status = sub.add_parser("status"); p_status.add_argument("plan_id")
    p_rec = sub.add_parser("reconcile"); p_rec.add_argument("plan_id"); p_rec.add_argument("--dry-run", action="store_true")
    p_start = sub.add_parser("start"); p_start.add_argument("request")
    p_start.add_argument("--planner", default="codex", choices=["codex", "gpt", "json"])
    p_start.add_argument("--plan-doc", default=None)
    p_cont = sub.add_parser("continue"); p_cont.add_argument("plan_id")
    p_cont.add_argument("--max-steps", type=int, default=10)
    p_rs = sub.add_parser("run-status"); p_rs.add_argument("plan_id")
    args = parser.parse_args()

    store = Store(STORE_ROOT)

    if args.cmd == "plan":
        request = contracts.UserRequest.from_dict(contracts.load_json(args.request))
        if args.planner == "codex":
            from orchestrator.codex_planner import CodexCLIPlanner
            from orchestrator.gpt_planner import GPTPlannerError
            from pathlib import Path as _P
            cp = CodexCLIPlanner(_P(request.target_repo).resolve(), plan_id=args.plan_id,
                                 evidence_dir=_P(STORE_ROOT).parent / "planner-evidence")
            try:
                plan, _ev = cp.plan(request)
            except GPTPlannerError as exc:
                print(f"PLAN_NOT_SAVED: {exc}")
                print("STATUS: (nothing saved — approval unavailable, materialization forbidden)")
                return 2
            store.save_request(request.to_dict())   # immutable user request (P2-03)
            store.save_plan(plan, status="PLANNED")  # LAST step (failure atomicity)
            print("PLAN_CREATED")
            print(json.dumps({"planner": "codex", "plan_id": plan.plan_id, "status": "PLANNED",
                              "tasks": [t.task_key for t in plan.tasks],
                              "requires_human_approval": plan.requires_human_approval},
                             ensure_ascii=False, indent=1))
            print("STATUS = PLANNED | HUMAN_APPROVAL_REQUIRED = YES")
            print(f"NEXT: orchestrate show {plan.plan_id} ; orchestrate approve {plan.plan_id}")
            return 0
        if args.planner == "gpt":
            # P2-02: model produces ONLY the semantic draft; host fields are local.
            from orchestrator.gpt_planner import GPTPlanner, GPTPlannerError
            from pathlib import Path as _P
            gp = GPTPlanner(_P(request.target_repo).resolve(), plan_id=args.plan_id,
                            evidence_dir=_P(STORE_ROOT).parent / "planner-evidence")
            try:
                plan, _ev = gp.plan(request)
            except GPTPlannerError as exc:
                print(f"PLAN_NOT_SAVED: {exc}")
                print("STATUS: (nothing saved — approval unavailable, materialization forbidden)")
                return 2
            store.save_request(request.to_dict())   # immutable user request (P2-03)
            store.save_plan(plan, status="PLANNED")  # LAST step (failure atomicity)
            print("PLAN_CREATED")
            print(json.dumps({"planner": "gpt", "plan_id": plan.plan_id, "status": "PLANNED",
                              "tasks": [t.task_key for t in plan.tasks],
                              "requires_human_approval": plan.requires_human_approval},
                             ensure_ascii=False, indent=1))
            print("STATUS = PLANNED | HUMAN_APPROVAL_REQUIRED = YES")
            print(f"NEXT: orchestrate show {plan.plan_id} ; orchestrate approve {plan.plan_id}")
            return 0
        if not args.plan_doc:
            print("--planner json requires --plan-doc <path>")
            return 2
        plan_doc = contracts.load_json(args.plan_doc)
        if "{{" in json.dumps(plan_doc):  # template auto-ids
            slug = request.request_id.removeprefix("req-")[:20]
            plan_doc["plan_id"] = f"plan-{slug}"
        planner = JsonPlanner(plan_doc)
        plan = planner.plan(request)
        plan.validate_dag()
        store.save_request(request.to_dict())       # immutable user request (P2-03)
        store.save_plan(plan, status="PLANNED")
        print("PLAN_CREATED")
        print(json.dumps({"planner": "json", "plan_id": plan.plan_id, "status": "PLANNED",
                          "tasks": [t.task_key for t in plan.tasks],
                          "requires_human_approval": plan.requires_human_approval},
                         ensure_ascii=False, indent=1))
        print("STATUS = PLANNED | HUMAN_APPROVAL_REQUIRED = YES")
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
        engines = {}
        if args.cmd == "reconcile" and not args.dry_run:
            # composition root: real decision engines wired ONLY for the
            # executing reconcile path — status/--dry-run never invoke Codex
            from orchestrator.final_gate import (CodexArbitrationEngine,
                                                 CodexFinalGateEngine)
            engines = {"final_gate_engine": CodexFinalGateEngine(),
                       "arbitration_engine": CodexArbitrationEngine()}
        result = Reconciler(store, beads, mail, **engines).reconcile_plan(
            args.plan_id, execute=(args.cmd == "reconcile" and not args.dry_run))
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "start":
        request = contracts.UserRequest.from_dict(contracts.load_json(args.request))
        if args.planner == "json":
            if not args.plan_doc:
                print("--planner json requires --plan-doc")
                return 2
            import copy as _copy
            doc = _copy.deepcopy(contracts.load_json(args.plan_doc))
            if "PLACEHOLDER" in (doc.get("plan_id", ""), doc.get("request_id", "")):
                import uuid as _uuid
                doc["plan_id"] = f"plan-{request.request_id.removeprefix('req-')[:20]}-{_uuid.uuid4().hex[:6]}"
            plan = JsonPlanner(doc).plan(request)
        elif args.planner == "gpt":
            from orchestrator.gpt_planner import GPTPlanner, GPTPlannerError
            gp = GPTPlanner(_repo_path(request.target_repo),
                            evidence_dir=STORE_ROOT.parent / "planner-evidence")
            try:
                plan, _ = gp.plan(request)
            except GPTPlannerError as exc:
                print(f"PLAN_NOT_SAVED: {exc}"); return 2
        else:
            from orchestrator.codex_planner import CodexCLIPlanner
            from orchestrator.gpt_planner import GPTPlannerError
            cp = CodexCLIPlanner(_repo_path(request.target_repo),
                                 evidence_dir=STORE_ROOT.parent / "planner-evidence")
            try:
                plan, _ = cp.plan(request)
            except GPTPlannerError as exc:
                print(f"PLAN_NOT_SAVED: {exc}"); return 2
        plan.validate_dag()
        store.save_request(request.to_dict())
        store.save_plan(plan, status="PLANNED")
        print(json.dumps({"PLAN_CREATED": True, "plan_id": plan.plan_id,
                          "planner": args.planner, "status": "PLANNED",
                          "tasks": [t.task_key for t in plan.tasks],
                          "HUMAN_APPROVAL_REQUIRED": True,
                          "next": f"orchestrate approve {plan.plan_id}"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "continue":
        plan = store.plan(args.plan_id)
        beads = BeadsAdapter(_repo_path(plan.target_repo))
        mail = AgentMailAdapter(_repo_path(plan.target_repo))
        from orchestrator.run_controller import RunController
        from pathlib import Path as _P
        ctrl = RunController(store, beads, mail,
                             evidence_dir=_P(STORE_ROOT).parent / "planner-evidence")
        result = ctrl.continue_run(args.plan_id, max_steps=args.max_steps)
        print(json.dumps(result.to_dict(), ensure_ascii=False))  # single-line JSON
        return 0

    if args.cmd == "run-status":
        plan = store.plan(args.plan_id)
        beads = BeadsAdapter(_repo_path(plan.target_repo))
        mail = AgentMailAdapter(_repo_path(plan.target_repo))
        from orchestrator.run_controller import RunController
        ctrl = RunController(store, beads, mail)  # read-only: no engines constructed
        print(json.dumps(ctrl.snapshot(args.plan_id), ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
