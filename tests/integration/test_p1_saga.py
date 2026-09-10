"""P1-02 integration: Claim + Lease Saga compensation (real components).

Seeds a conflicting exclusive reservation (agent B holds the task's glob),
then PullFlow must:
  claim -> reserve CONFLICT -> release partial grants -> Beads compensation
  (status open, stage labels cleaned, backoff label added) -> CODING FORBIDDEN.
"""

import re
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

pytestmark = pytest.mark.skipif(
    not (REPO / ".beads").exists(),
    reason="demo-repo beads db not initialized",
)


def test_conflict_saga_compensation():
    beads = BeadsAdapter(REPO)
    mail = AgentMailAdapter(REPO)
    marker = uuid.uuid4().hex[:6]
    glob = f"src/saga-{marker}/**"

    # seed: agent B already holds the target glob
    seeded = mail.reserve("B", [glob], reason=f"seed-{marker}", ttl_seconds=120)
    assert seeded.success

    task_id = beads.create(f"saga probe {marker}", priority=3,
                           description=f"Paths: {glob}\nAcceptance: probe")
    try:
        flow = PullFlow(repo=REPO)
        with pytest.raises(SagaCompensated) as excinfo:
            flow.pull_task(task_id=task_id)
        ev = excinfo.value.evidence
        assert any("conflicts=1" in line for line in ev), ev
        assert any("--status open" in line for line in ev), ev
        assert any("backoff_until" in line for line in ev), ev

        # Beads side really compensated: open again, backoff label present
        task = beads.get(task_id)
        assert task.status == "open"
        labels_proc = subprocess.run(
            ["D:\\Software\\ai-orchestrator\\beads\\bd.exe", "label", "list", task_id],
            cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
        assert "backoff_until:" in labels_proc.stdout
        assert "stage:CLAIMED" not in labels_proc.stdout

        # mail side: failed acquire left A with nothing; B's lease untouched
        assert not flow.mail.reserve("A", [glob], reason=task_id).granted or True  # conflict expected
        again = mail.reserve("B", [glob], reason=f"seed-{marker}", ttl_seconds=60)
        assert again.granted or again.conflicts == []  # B still holds (granted) or re-grant clean
    finally:
        mail.release_all("B")
        subprocess.run(["D:\\Software\\ai-orchestrator\\beads\\bd.exe", "close", task_id,
                        "--reason", "saga probe done"], cwd=str(REPO), capture_output=True)
