"""CodexCLIPlanner backend-specific units (subprocess mocked; real run covered
by scripts/p2_smoke_gpt.py codex smoke — see P2-02 report)."""

import json
import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator import contracts  # noqa: E402
from orchestrator.codex_planner import CodexCLIPlanner  # noqa: E402
from orchestrator.gpt_planner import GPTPlannerError  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "sandbox" / "demo-repo"


def request():
    return contracts.UserRequest(request_id="req-cx-1", target_repo=str(REPO),
                                 title="codex probe", description="d", constraints=[])


def make_planner(monkeypatch, *, rc=0, last_json=None, stdout="tokens used\n1,000"):
    p = CodexCLIPlanner(REPO, plan_id="plan-cx-test")
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        calls["stdin"] = kw.get("input", "")
        # emulate -o last-message file
        for i, a in enumerate(cmd):
            if a == "-o" and last_json is not None:
                Path(cmd[i + 1]).write_text(json.dumps(last_json), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return p, calls


def valid_draft():
    return {"summary": "s",
            "tasks": [{"task_key": "cx-task", "title": "codex probe task", "description": "d",
                       "target_paths": ["src/order.py"], "acceptance_criteria": "criterion met",
                       "dependencies": [], "executor_role": "zcode",
                       "reviewer_role": "antigravity", "risk_level": "LOW"}],
            "risks": [], "assumptions": []}


def test_command_shape_and_readonly_ephemeral(monkeypatch):
    p, calls = make_planner(monkeypatch, last_json=valid_draft())
    plan, ev = p.plan(request())
    cmd = calls["cmd"]
    assert cmd[0].endswith("codex") or cmd[0].endswith("codex.exe") or "codex" in cmd[0]
    joined = " ".join(cmd)
    assert "-s read-only" in joined and "--ephemeral" in joined
    assert "--skip-git-repo-check" in joined and "--ignore-rules" in joined
    assert '--output-schema' in joined and '"high"' in joined
    assert calls["stdin"].startswith("# GPT Planner Prompt")  # versioned prompt leads
    assert "REJECTED" not in calls["stdin"]                    # first attempt: no repair block
    assert plan.tasks[0].task_key == "cx-task"
    assert ev.attempts[0]["validation_result"] == "valid"


def test_codex_failure_fails_closed(monkeypatch):
    p, _ = make_planner(monkeypatch, rc=1, last_json=None)
    with pytest.raises(GPTPlannerError) as ei:
        p.plan(request())
    assert ei.value.error_category == "api_error"


def test_non_json_final_message_fails_closed(monkeypatch):
    # emulate -o writing garbage
    p = CodexCLIPlanner(REPO, plan_id="plan-cx-bad")

    def fake_run(cmd, **kw):
        for i, a in enumerate(cmd):
            if a == "-o":
                Path(cmd[i + 1]).write_text("not json at all", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GPTPlannerError):
        p.plan(request())  # two attempts both malformed -> PLAN_NOT_SAVED
