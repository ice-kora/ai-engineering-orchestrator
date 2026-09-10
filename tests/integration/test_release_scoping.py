"""P2-00: task-scoped release granularity (GPT precheck scenario).

Same agent holds reservations for TWO different tasks; releasing Task-A must
remove exactly Task-A's reservation and leave Task-B's intact (verified both
via `list` and via a third-party conflict probe). Guards against the old
release_all() foot-gun of nuking sibling-task leases.
"""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters.agent_mail import AgentMailAdapter  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"

pytestmark = pytest.mark.skipif(
    not (REPO / ".beads").exists(),
    reason="demo-repo not initialized",
)


def test_release_task_a_spare_task_b():
    mail = AgentMailAdapter(REPO)
    marker = uuid.uuid4().hex[:6]
    task_a, task_b = f"rel-a-{marker}", f"rel-b-{marker}"
    path_a, path_b = f"src/{task_a}.txt", f"src/{task_b}.txt"

    try:
        res_a = mail.reserve("A", [path_a], reason=task_a, ttl_seconds=120)
        res_b = mail.reserve("A", [path_b], reason=task_b, ttl_seconds=120)
        assert res_a.success and res_b.success, f"seed failed: {res_a} / {res_b}"
        id_a = res_a.granted[0]["id"]

        # release ONLY task A — via explicit ids AND via reason-mapping fallback
        out = mail.release_for_task("A", task_a, reservation_ids=[str(id_a)])
        assert out["released"] == 1 and out["task_reservations_remaining"] == 0, out

        active = mail.active_reservations()
        reasons = {r["reason"] for r in active}
        assert task_a not in reasons, f"task-A reservation survived scoped release: {active}"
        assert task_b in reasons, f"task-B reservation was collateral damage: {active}"

        # third-party view: B's path must still conflict for another agent
        probe = mail.reserve("B", [path_b], reason=f"probe-{marker}", ttl_seconds=60)
        assert probe.conflicts and not probe.granted, "task-B lease lost after releasing task-A"

        # fallback path: release B via reason-mapping only (no explicit ids)
        out_b = mail.release_for_task("A", task_b)
        assert out_b["released"] == 1 and out_b["task_reservations_remaining"] == 0, out_b
        assert task_b not in {r["reason"] for r in mail.active_reservations()}
    finally:
        mail.release_all("A")
        mail.release_all("B")
