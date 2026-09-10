"""Unit: handover/review payload builders + Draft 2020-12 validation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters import handover  # noqa: E402


def make_handover(**over):
    base = dict(
        task_id="demo-repo-ujn", iteration=1,
        worktree_path=r"D:\wt", branch_name="agent/zcode/demo-repo-ujn",
        base_commit="0123456789abcdef0123456789abcdef01234567",
        head_commit="fedcba9876543210fedcba9876543210fedcba98",
        affected_files=["src/x.py"],
        test_command="python -m unittest", test_exit_code=0,
        total_tests=3, passed_tests=3, failed_tests=0, output_summary="OK",
        deliverable_summary="d", known_risks=[],
    )
    base.update(over)
    return handover.build_handover(**base)


def test_handover_valid():
    assert handover.validate_payload(make_handover(), "handover") == []


@pytest.mark.parametrize("mutation", [
    {"task_id": "123"},                       # bad id shape
    {"iteration": 4},                         # circuit breaker bound
    {"branch_name": "feature/x"},             # branch convention
    {"affected_files": []},                   # minItems 1
])
def test_handover_invalid(mutation):
    payload = make_handover(**mutation)
    assert handover.validate_payload(payload, "handover")


def test_review_schema_roundtrip():
    report = {
        "schema_version": "1.1.0", "task_id": "demo-repo-ujn", "iteration": 1,
        "reviewer": "agent-antigravity", "verdict": "CHANGES_REQUESTED",
        "verified_head_commit": "fedcba9",
        "findings": [{
            "severity": "MAJOR", "file_path": "src/x.py", "line_number": 10,
            "issue_type": "LOGIC_BUG", "description": "missing discount",
            "actionable_fix": "apply discount at >=100",
        }],
    }
    assert handover.validate_payload(report, "review") == []
    report["verdict"] = "MAYBE"
    assert handover.validate_payload(report, "review")
