"""P1 entry point: /pull-task driver.

Usage (ZCode, human-triggered):
    uv run python scripts/pull_task.py [--task <id>] [--repo sandbox/demo-repo]

Also referenced by .zcode/commands/pull-task.md for slash-command discovery.
Exit codes: 0 = plan ready (context printed); 2 = saga compensated (DO NOT CODE);
3 = hard error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.pull_flow import PullFlow, SagaCompensated  # noqa: E402
from adapters.config import DEMO_REPO  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="pull-task")
    parser.add_argument("--task", default=None, help="specific task id (default: best priority)")
    parser.add_argument("--repo", default=str(DEMO_REPO))
    args = parser.parse_args()

    flow = PullFlow(repo=Path(args.repo))
    try:
        plan = flow.pull_task(task_id=args.task)
    except SagaCompensated as exc:
        print("== SAGA COMPENSATED — CODING FORBIDDEN ==")
        print(exc)
        print("-- evidence --")
        for line in exc.evidence:
            print(" ", line)
        return 2
    except Exception as exc:
        print(f"pull-task failed: {exc}")
        for line in flow.evidence:
            print(" ", line)
        return 3

    r = plan.reservation
    print("== PULL-TASK READY — execution context ==")
    print(f"task_id           : {plan.task.id}")
    print(f"title             : {plan.task.title}")
    print(f"acceptance        : {plan.task.acceptance_criteria() or '(none)'}")
    print(f"actor             : {plan.actor}")
    print(f"worktree (abs)    : {plan.worktree}")
    print(f"branch            : {plan.branch}")
    print(f"base commit       : {plan.base_commit[:12]}")
    print(f"reserved paths    : {[g.get('path_pattern') for g in r.granted]}")
    print(f"lease expires_ts  : {r.granted[0].get('expires_ts') if r.granted else '-'}")
    print(f"mail thread       : {plan.task.id}  ([{plan.task.id}] subject prefix)")
    print("-- evidence --")
    for line in plan.evidence:
        print(" ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
