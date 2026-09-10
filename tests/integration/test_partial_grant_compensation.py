"""P2-00 hotfix: partial-grant Saga compensation under same-agent concurrency.

Real scenario (all live components, no mocks):
  * Agent A (zcode role) ALREADY holds Task-B's lease  (path-b, reason=task-b)
  * Agent B (antigravity role) holds path-h            (conflict source)
  * Beads Task-A targets [path-h, path-a-free]         (multi-path reserve)
  * REAL reserve returns granted=[path-a-free] + conflicts=[path-h] + exit 0
    (empirical platform behaviour, see runtime-errata E-03)

After PullFlow compensation we MUST assert:
  1. Task-A's partial grant is released (third party can take path-a-free)
  2. Task-A's Beads claim is compensated back to open (+backoff label)
  3. Task-B's lease — held by the SAME agent — SURVIVES
  4. Task-B's path still conflicts for a third party
  5. No coding: no agent/zcode/<task-a> branch was created
"""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters.agent_mail import AgentMailAdapter  # noqa: E402
from adapters.beads import BeadsAdapter  # noqa: E402
from adapters.pull_flow import PullFlow, SagaCompensated  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"
BD = r"D:\Software\ai-orchestrator\beads\bd.exe"

pytestmark = pytest.mark.skipif(
    not (REPO / ".beads").exists(),
    reason="demo-repo not initialized",
)


def test_partial_grant_compensation_spares_sibling_task():
    beads = BeadsAdapter(REPO)
    mail = AgentMailAdapter(REPO)
    marker = uuid.uuid4().hex[:6]
    path_h = f"src/pgc-{marker}-held.txt"       # held by B (conflict source)
    path_a_free = f"src/pgc-{marker}-free.txt"  # free -> partial grant for Task-A
    path_b = f"src/pgc-{marker}-taskb.txt"      # Task-B lease, held by A (same agent!)

    task_b_reason = f"pgc-task-b-{marker}"
    # same-agent sibling lease + conflict source
    seeded_b = mail.reserve("A", [path_b], reason=task_b_reason, ttl_seconds=300)
    seeded_h = mail.reserve("B", [path_h], reason=f"holder-{marker}", ttl_seconds=300)
    assert seeded_b.success and seeded_h.success

    task_a = beads.create(f"partial-grant probe {marker}", priority=3,
                          description=f"Paths: {path_h}, {path_a_free}\nAcceptance: probe")
    flow = PullFlow(repo=REPO)
    try:
        with pytest.raises(SagaCompensated) as excinfo:
            flow.pull_task(task_id=task_a)
        ev = excinfo.value.evidence
        assert any("release --ids" in line for line in ev), f"scoped release missing: {ev}"
        assert any("--status open" in line for line in ev), ev

        # (1) Task-A partial grant released: third party can now take it
        probe_a = mail.reserve("B", [path_a_free], reason=f"probe-a-{marker}", ttl_seconds=60)
        assert probe_a.success, f"partial grant not released: {probe_a}"
        mail.release_for_task("B", f"probe-a-{marker}")

        # (2) Task-A claim compensated: open + backoff label
        assert beads.get(task_a).status == "open"
        labels = subprocess.run([BD, "label", "list", task_a], cwd=str(REPO),
                                capture_output=True, text=True, encoding="utf-8").stdout
        assert "backoff_until:" in labels and "stage:CLAIMED" not in labels

        # (3) Task-B lease of the SAME agent SURVIVES the compensation
        active = mail.active_reservations()
        assert any(r["reason"] == task_b_reason for r in active), (
            f"sibling Task-B lease destroyed by compensation: {active}")

        # (4) Task-B path still conflicts for a third party
        probe_b = mail.reserve("B", [path_b], reason=f"probe-b-{marker}", ttl_seconds=60)
        assert probe_b.conflicts and not probe_b.granted, "Task-B lease lost"

        # (5) No coding: no task-A branch was created
        branches = subprocess.run(["git", "-C", str(REPO), "branch", "--list",
                                   f"agent/zcode/{task_a}"],
                                  capture_output=True, text=True, encoding="utf-8").stdout
        assert not branches.strip(), f"coding branch created despite compensation: {branches}"
    finally:
        mail.release_for_task("A", task_b_reason)
        mail.release_for_task("B", f"holder-{marker}")
        subprocess.run([BD, "close", task_a, "--reason", "probe done"],
                       cwd=str(REPO), capture_output=True)
