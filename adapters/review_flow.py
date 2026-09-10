"""Review flow (P1-03 handover + P1-04 independent agy review).

The reviewer works in the PER_AGENT agent-antigravity worktree checked out at a
DETACHED head of the implementation commit (git forbids the same branch in two
worktrees; detached = read-only viewing context). Reviewer never edits.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from adapters import handover
from adapters.agent_mail import AgentMailAdapter
from adapters.antigravity import AntigravityAdapter
from adapters.beads import BeadsAdapter


def run_tests(worktree: Path) -> dict:
    """Run the stdlib unittest suite inside a worktree; return test_evidence fields."""
    proc = subprocess.run(
        ["python", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
        cwd=str(worktree), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    ran = re.search(r"Ran (\d+) tests?", out)
    total = int(ran.group(1)) if ran else 0
    failed = len(re.findall(r"\bFAIL\b|\bERROR\b", out))
    return {
        "command": "python -m unittest discover -s tests -t . -v",
        "exit_code": proc.returncode,
        "total_tests": total,
        "passed_tests": max(0, total - failed),
        "failed_tests": failed,
        "output_summary": out.strip()[-800:],
    }


def git_ref(worktree: Path, ref: str) -> str:
    proc = subprocess.run(["git", "-C", str(worktree), "rev-parse", ref],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse {ref} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def changed_files(worktree: Path, base: str, head: str) -> list[str]:
    proc = subprocess.run(["git", "-C", str(worktree), "diff", "--name-only", f"{base}..{head}"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def build_and_validate_handover(*, task_id: str, iteration: int, worktree: Path,
                                branch: str, base: str, deliverable_summary: str,
                                known_risks: list[str] | None = None,
                                test_evidence: dict | None = None) -> dict:
    head = git_ref(worktree, "HEAD")
    payload = handover.build_handover(
        task_id=task_id, iteration=iteration,
        worktree_path=str(worktree.resolve()), branch_name=branch,
        base_commit=base, head_commit=head,
        affected_files=changed_files(worktree, base, head),
        test_command=(test_evidence or run_tests(worktree))["command"],
        test_exit_code=(test_evidence or run_tests(worktree))["exit_code"],
        total_tests=(test_evidence or run_tests(worktree))["total_tests"],
        passed_tests=(test_evidence or run_tests(worktree))["passed_tests"],
        failed_tests=(test_evidence or run_tests(worktree))["failed_tests"],
        output_summary=(test_evidence or run_tests(worktree))["output_summary"],
        deliverable_summary=deliverable_summary, known_risks=known_risks,
    )
    errors = handover.validate_payload(payload, "handover")
    if errors:
        raise ValueError("TaskHandoverPayload schema violations: " + "; ".join(errors))
    return payload


def ensure_reviewer_worktree(repo: Path, head_commit: str) -> Path:
    """agent-antigravity PER_AGENT worktree at detached head (read-only context)."""
    ag_wt = repo / "worktrees" / "agent-antigravity"
    if not ag_wt.exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "add",
                        "worktrees/agent-antigravity", "-b", "agent/antigravity/home", "main"],
                       capture_output=True, text=True, check=True)
    subprocess.run(["git", "-C", str(ag_wt), "checkout", "--detach", head_commit],
                   capture_output=True, text=True, check=True)
    return ag_wt


def review_iteration(*, repo: Path, task: dict, acceptance: str,
                     handover_payload: dict) -> dict:
    """One review round: mail handover -> agy structured review -> payload."""
    mail = AgentMailAdapter(repo)
    task_id = handover_payload["task_id"]
    iteration = handover_payload["iteration"]

    mail.send("A", "B", task_id,
              f"[{task_id}] Handover for review (iteration {iteration})",
              "```json\n" + __import__("json").dumps(handover_payload, ensure_ascii=False, indent=1) + "\n```")

    head = handover_payload["git_context"]["head_commit"]
    ag_wt = ensure_reviewer_worktree(repo, head)
    adapter = AntigravityAdapter(ag_wt)
    report = adapter.review(task_id, handover_payload, acceptance)

    verdict = report["verdict"]
    mail.send("B", "A", task_id,
              f"[{task_id}] Review verdict: {verdict} (iteration {iteration})",
              "```json\n" + __import__("json").dumps(
                  {k: v for k, v in report.items() if not k.startswith("_")},
                  ensure_ascii=False, indent=1) + "\n```")
    return report
