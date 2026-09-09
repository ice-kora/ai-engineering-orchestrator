"""P0-12: unified task ID chain (Beads task == mail thread == reservation
reason == git branch suffix == commit trailer).

The Beads/Mail halves SKIP-BLOCKED until components are installed; the
git-side conventions are verified live against sandbox/demo-repo.

CLI hooks for demo.ps1:
    python test_task_id_unification.py --demo-claim bd-xxxx
    python test_task_id_unification.py --demo-close bd-xxxx
"""

import asyncio
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_REPO = PROJECT_ROOT / "sandbox" / "demo-repo"

BRANCH_RE = re.compile(r"^agent/[a-z0-9_-]+/bd-[0-9a-z]+$")
TASK_RE = re.compile(r"^bd-[0-9a-z]{4,10}$")


def git(*args: str, cwd: Path = DEMO_REPO) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed:\n{proc.stderr}")
    return proc.stdout.strip()


def test_branch_and_commit_conventions():
    """v1.0 §6.2 / v1.1 §5.2: branch `agent/<agent>/<task-id>`, commit `[bd-xxx]`.

    Uses the branches produced by the worktree tests (agent/*/bd-wt00xx),
    which follow exactly the mandated pattern.
    """
    if not (DEMO_REPO / ".git").exists():
        pytest.skip("demo-repo not initialized (run test_git_worktree.py first)")
    branches = [b.strip() for b in git("branch", "--list", "agent/*").splitlines()] or \
               [b.strip() for b in git("branch", "--all", "--list", "*bd-*").splitlines()]
    assert branches, "no agent branches found; run test_git_worktree.py first"
    for br in branches:
        assert BRANCH_RE.match(br), f"branch violates convention: {br}"

    log = git("log", "--grep", "\\[bd-", "--oneline", "-E", "--all")
    assert "[bd-wt0001]" in log and "[bd-wt0002]" in log, (
        "commits missing [bd-xxx] trailer:\n" + log
    )


def test_task_id_shape():
    assert TASK_RE.match("bd-a3f8e9")
    assert not TASK_RE.match("bd-Short")
    assert not TASK_RE.match("123")


# ---------------- Beads+Mail chain (SKIP-BLOCKED without components) ---------

bd = None
try:
    import shutil
    bd = shutil.which("bd")
except Exception:
    pass

from _mail import AGENTS, PROJECT_KEY, mail_available, stdio_params  # noqa: E402
from test_agent_mail import call  # noqa: E402

pytestmark_chain = pytest.mark.skipif(
    bd is None or not mail_available(),
    reason="BLOCKED: bd or mcp-agent-mail not installed",
)


@pytest.mark.skipif(bd is None or not mail_available(), reason="BLOCKED: components not installed")
def test_unified_id_chain():
    """One task id visible as: beads task, mail thread, reservation reason."""
    proc = subprocess.run([bd, "-C", str(DEMO_REPO), "create", "P0 unified id probe", "-p", "1"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"bd-[0-9a-z]{4,10}", proc.stdout)
    assert m, f"no task id: {proc.stdout}{proc.stderr}"
    task = m.group(0)

    async def chain() -> tuple[bool, bool]:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        thread_ok = reason_ok = False
        async with stdio_client(stdio_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await call(session, "send_message", {
                    "project_key": PROJECT_KEY, "sender": AGENTS[0], "recipient": AGENTS[1],
                    "subject": f"[{task}] Start", "body": "unified id chain probe",
                    "thread_id": task,
                })
                topic = await call(session, "fetch_topic", {
                    "project_key": PROJECT_KEY, "thread_id": task,
                })
                thread_ok = task in str(topic)
                res = await call(session, "file_reservation_paths", {
                    "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                    "paths": ["src/calculator.py"], "ttl_seconds": 30,
                    "exclusive": True, "reason": task,
                })
                reason_ok = task in str(res)
                await call(session, "release_file_reservations", {
                    "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                })
        return thread_ok, reason_ok

    thread_ok, reason_ok = asyncio.run(chain())
    assert thread_ok, "mail thread id != beads task id"
    assert reason_ok, "reservation reason != beads task id"
    subprocess.run([bd, "-C", str(DEMO_REPO), "close", task, "probe done"], capture_output=True)


# ---------------- demo.ps1 CLI hooks ------------------------------------------

if __name__ == "__main__":
    if not mail_available():
        print("BLOCKED: mcp-agent-mail not installed")
        sys.exit(2)
    task = sys.argv[sys.argv.index("--demo-claim") + 1] if "--demo-claim" in sys.argv else None
    close_task = sys.argv[sys.argv.index("--demo-close") + 1] if "--demo-close" in sys.argv else None
    task = close_task or task

    async def demo_step(close: bool):
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(stdio_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for agent in AGENTS:
                    await call(session, "register_agent", {"agent_name": agent, "project_key": PROJECT_KEY})
                if close:
                    await call(session, "send_message", {
                        "project_key": PROJECT_KEY, "sender": AGENTS[1], "recipient": AGENTS[0],
                        "subject": f"[{task}] Review: APPROVED", "body": "cross review approved",
                        "thread_id": task,
                    })
                    await call(session, "release_file_reservations", {
                        "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                    })
                    print(f"demo-close done: review message + release for {task}")
                else:
                    await call(session, "file_reservation_paths", {
                        "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                        "paths": ["src/calculator.py", "tests/test_calculator.py"],
                        "ttl_seconds": 600, "exclusive": True, "reason": task,
                    })
                    await call(session, "send_message", {
                        "project_key": PROJECT_KEY, "sender": AGENTS[0], "recipient": AGENTS[1],
                        "subject": f"[{task}] Start", "body": "claimed + reserved, coding starts",
                        "thread_id": task,
                    })
                    print(f"demo-claim done: reserved + Start message for {task}")

    asyncio.run(demo_step(close=close_task is not None))
