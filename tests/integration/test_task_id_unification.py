"""P0-12: unified task ID chain (Beads task == mail thread == reservation
reason == git branch suffix == commit trailer).

- Beads side runs live (bd v1.2.2 installed).
- Mail side runs live via the am CLI.
- Git-side conventions verified against sandbox/demo-repo.
- demo.ps1 hooks: --demo-claim <task> / --demo-close <task>.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _mail import DEMO_REPO, am, am_json, get_identities, mail_available, register_identities

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BD = Path(r"D:\Software\ai-orchestrator\beads\bd.exe")

BRANCH_RE = re.compile(r"^agent/[a-z0-9_-]+/[a-z0-9-]+-[a-z0-9]+$")
TASK_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-[a-z0-9]{3,8}$")  # real format: <repo-prefix>-<rand4>


def git(*args: str, cwd: Path = PROJECT_ROOT / "sandbox" / "demo-repo") -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed:\n{proc.stderr}")
    return proc.stdout.strip()


def test_branch_and_commit_conventions():
    """branch `agent/<agent>/<task-id>` + commit `[<task-id>]` trailer."""
    branches = [b.strip() for b in git("branch", "--list", "agent/*").splitlines()]
    assert branches, "no agent branches; run test_git_worktree.py first"
    for br in branches:
        assert BRANCH_RE.match(br), f"branch violates convention: {br}"
    log = git("log", "--grep", "\\[bd-", "--oneline", "-E", "--all")
    assert "[bd-wt0001]" in log, "commits missing [bd-xxx] trailer"


def test_task_id_shape():
    assert TASK_RE.match("bd-a3f8e9")
    assert TASK_RE.match("demo-repo-qgz")  # real prefix form
    assert not TASK_RE.match("123")


@pytest.mark.skipif(not BD.exists() or not mail_available(), reason="BLOCKED: bd or am not installed")
def test_unified_id_chain(tmp_path):
    """One task id as: beads task, mail thread id, reservation reason."""
    a, b = register_identities()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "aeo-dev"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "aeo-dev@local"], capture_output=True, check=True)
    proc = subprocess.run([str(BD), "init", "--non-interactive"], cwd=str(repo),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    created = subprocess.run([str(BD), "q", "unified id probe", "-p", "1"], cwd=str(repo),
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"([a-z0-9][a-z0-9-]*-[a-z0-9]{3,8})\s*$", created.stdout.strip(), re.M)
    assert m, f"no task id: {created.stdout!r} {created.stderr!r}"
    task = m.group(1)

    # mail: thread id == task id
    sent = am_json("mail", "send", "--project", DEMO_REPO, "--from", a, "--to", b,
                   "--subject", f"[{task}] Start", "--body", "unified id chain probe",
                   "--thread-id", task, "--json")
    assert sent
    inbox = am_json("mail", "inbox", "--project", DEMO_REPO, "--agent", b, "--json")
    assert task in json.dumps(inbox), "thread id != beads task id in inbox"

    # reservation: reason == task id
    res = am_json("file_reservations", "reserve", "--ttl", "120", "--exclusive",
                  "--reason", task, DEMO_REPO, a, "src/calculator.py")
    assert res.get("granted") and res["granted"][0]["reason"] == task
    am("file_reservations", "release", DEMO_REPO, a)

    # beads: close
    closed = subprocess.run([str(BD), "close", task, "--reason", "probe done"], cwd=str(repo),
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert closed.returncode == 0, closed.stderr


if __name__ == "__main__":
    if not mail_available():
        print("BLOCKED: am CLI not installed")
        sys.exit(2)
    task = sys.argv[sys.argv.index("--demo-claim") + 1] if "--demo-claim" in sys.argv else None
    close_task = sys.argv[sys.argv.index("--demo-close") + 1] if "--demo-close" in sys.argv else None
    task = close_task or task
    a, b = register_identities()

    if close_task:
        am_json("mail", "send", "--project", DEMO_REPO, "--from", b, "--to", a,
                "--subject", f"[{task}] Review: APPROVED", "--body", "cross review approved",
                "--thread-id", task, "--json")
        am("file_reservations", "release", DEMO_REPO, a)
        print(f"demo-close done: review message + release for {task}")
    else:
        am_json("file_reservations", "reserve", "--ttl", "600", "--exclusive",
                "--reason", task, DEMO_REPO, a,
                "src/calculator.py", "tests/test_calculator.py")
        am_json("mail", "send", "--project", DEMO_REPO, "--from", a, "--to", b,
                "--subject", f"[{task}] Start", "--body", "claimed + reserved, coding starts",
                "--thread-id", task, "--json")
        print(f"demo-claim done: reserved + Start message for {task}")
