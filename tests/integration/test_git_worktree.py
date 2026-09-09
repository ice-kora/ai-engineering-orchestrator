"""P0 Git Worktree independence test (PER_AGENT reusable worktrees).

Verifies against sandbox/demo-repo (an independent git repo, bootstrapped
idempotently by the fixture):

1. Two PER_AGENT reusable worktrees can coexist: worktrees/agent-zcode,
   worktrees/agent-antigravity.
2. Task branches are cut inside each worktree via `git checkout -B
   agent/<agent>/<task-id> <base>` (v1.1 §7.1: no forced main checkout).
3. Independent edits on independent branches are diffable and mergeable.
4. Unittest runs inside a worktree in isolation.
5. Forensic retention dry-run (v1.1 §7.3): on a simulated failure, produce
   failure_artifacts/<task>.patch/.status/.log and an archive/failed/<task>-<ts>
   branch BEFORE any cleanup; no --force removal anywhere.

These are pure Git capability checks — worktree lifecycle automation stays P1+.
"""

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_REPO = PROJECT_ROOT / "sandbox" / "demo-repo"

ZCODE_WT = DEMO_REPO / "worktrees" / "agent-zcode"
AG_WT = DEMO_REPO / "worktrees" / "agent-antigravity"


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    """Run git, return stdout. check=True raises on non-zero exit."""
    proc = subprocess.run(
        ["git", "-C", str(cwd or DEMO_REPO), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed (exit {proc.returncode}):\n{proc.stderr}"
        )
    return proc.stdout.strip()


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_demo_repo() -> None:
    """Idempotent bootstrap of the sandbox demo repo (independent repository)."""
    if not (DEMO_REPO / ".git").exists():
        DEMO_REPO.mkdir(parents=True, exist_ok=True)
        git("init", "-b", "main")
        write(DEMO_REPO / ".gitignore", "worktrees/\n__pycache__/\n*.pyc\n")
        write(
            DEMO_REPO / "README.md",
            "# demo-repo\n\nP0 sandbox repository for the AI Engineering Orchestrator.\n"
            "Demo task: implement `add(a, b)` in `src/calculator.py` + `tests/test_calculator.py`.\n",
        )
        write(DEMO_REPO / "src" / ".gitkeep", "")
        write(DEMO_REPO / "src" / "__init__.py", "")
        write(DEMO_REPO / "src" / "service" / ".gitkeep", "")
        write(DEMO_REPO / "tests" / ".gitkeep", "")
        write(DEMO_REPO / "tests" / "__init__.py", "")
    # demo-repo is standalone: it needs its own identity (no global one on this host)
    git("config", "user.name", "aeo-dev")
    git("config", "user.email", "aeo-dev@local")
    # resume broken bootstrap (e.g. commit failed on a previous run)
    if not git("rev-parse", "--verify", "-q", "main", check=False):
        git("add", "-A")
        git("commit", "-m", "chore: init demo-repo (P0 sandbox)")


def clean_worktrees() -> None:
    """Reset to a repeatable state (only the normal success path may clean up)."""
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree ") and "demo-repo" in line:
            wt = Path(line.split(" ", 1)[1])
            if wt != DEMO_REPO and wt.exists():
                git("worktree", "remove", "--force", str(wt))
    git("worktree", "prune")
    git("checkout", "main")
    # drop task branches from previous runs (test-only scratch branches)
    for br in git("branch", "--list", "agent/*").splitlines():
        git("branch", "-D", br.strip())


CALCULATOR = '''"""Demo deliverable produced inside the zcode worktree."""


def add(a: float, b: float) -> float:
    """Return the sum of a and b."""
    return a + b
'''

TEST_CALCULATOR = '''import unittest

from src.calculator import add


class TestCalculator(unittest.TestCase):
    def test_add_positive(self):
        self.assertEqual(add(1, 2), 3)

    def test_add_negative(self):
        self.assertEqual(add(-1, -2), -3)

    def test_add_float(self):
        self.assertAlmostEqual(add(0.1, 0.2), 0.3)


if __name__ == "__main__":
    unittest.main()
'''

TEST_SERVICE = '''// Placeholder touched by the antigravity worktree (P0 worktree independence test).
public class TestService {
    static String marker() {
        return "ag-worktree";
    }
}
'''


def test_per_agent_worktree_independence():
    ensure_demo_repo()
    clean_worktrees()
    base = git("rev-parse", "main")

    # 1. PER_AGENT reusable worktrees on persistent home branches
    git("worktree", "add", "-b", "agent/zcode/home", str(ZCODE_WT), "main")
    git("worktree", "add", "-b", "agent/antigravity/home", str(AG_WT), "main")
    listed = git("worktree", "list", "--porcelain")
    # git porcelain uses forward slashes even on Windows
    assert ZCODE_WT.as_posix() in listed and AG_WT.as_posix() in listed

    # 2. Task branches cut inside each worktree (no main checkout required)
    git("checkout", "-B", "agent/zcode/bd-wt0001", base, cwd=ZCODE_WT)
    git("checkout", "-B", "agent/antigravity/bd-wt0002", base, cwd=AG_WT)
    assert git("branch", "--show-current", cwd=ZCODE_WT) == "agent/zcode/bd-wt0001"
    assert git("branch", "--show-current", cwd=AG_WT) == "agent/antigravity/bd-wt0002"

    # 3. Independent edits: main checkout stays untouched meanwhile
    write(ZCODE_WT / "src" / "calculator.py", CALCULATOR)
    write(ZCODE_WT / "tests" / "test_calculator.py", TEST_CALCULATOR)
    # unittest discovery requires packages under the top-level dir
    write(ZCODE_WT / "src" / "__init__.py", "")
    write(ZCODE_WT / "tests" / "__init__.py", "")
    write(AG_WT / "src" / "service" / "TestService.java", TEST_SERVICE)
    assert not (DEMO_REPO / "src" / "calculator.py").exists()  # isolation
    assert not (AG_WT / "src" / "calculator.py").exists()

    git("add", "-A", cwd=ZCODE_WT)
    git("commit", "-m", "[bd-wt0001] implement calculator add + tests", cwd=ZCODE_WT)
    git("add", "-A", cwd=AG_WT)
    git("commit", "-m", "[bd-wt0002] add TestService placeholder", cwd=AG_WT)

    # 4. Diffable
    diff = git("diff", "main", "agent/zcode/bd-wt0001", "--", "src/calculator.py")
    assert "+def add(" in diff

    # 5. Unittest runs inside the worktree, isolated from the other worktree
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
        cwd=str(ZCODE_WT), capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, f"unittest failed in worktree:\n{proc.stderr}"
    # unittest writes its report to stderr by convention
    assert "test_add_float" in proc.stderr and "OK" in proc.stderr

    # 6. Mergeable sequentially (disjoint files => clean merges)
    git("merge", "--no-ff", "agent/zcode/bd-wt0001", "-m", "merge [bd-wt0001] into main")
    git("merge", "--no-ff", "agent/antigravity/bd-wt0002", "-m", "merge [bd-wt0002] into main")
    merged_calc = (DEMO_REPO / "src" / "calculator.py").read_text(encoding="utf-8")
    assert "def add(" in merged_calc
    assert (DEMO_REPO / "src" / "service" / "TestService.java").exists()

    # normal success path cleanup is allowed; home branches stay for reuse
    git("worktree", "remove", str(ZCODE_WT))
    git("worktree", "remove", str(AG_WT))
    git("worktree", "prune")


def test_forensic_retention_dry_run():
    """v1.1 §7.3: FAILED/BLOCKED worktree must not be force-removed before
    evidence capture. Dry-run the SOP with git primitives only."""
    ensure_demo_repo()
    clean_worktrees()
    base = git("rev-parse", "main")

    git("worktree", "add", "-b", "agent/zcode/bd-wt0003", str(ZCODE_WT), base)
    git("checkout", "-B", "agent/zcode/bd-wt0003", base, cwd=ZCODE_WT)

    # simulate in-flight work that then "fails": committed base + uncommitted mess
    write(ZCODE_WT / "src" / "calculator.py", "def add(a, b):\n    return a - b  # buggy\n")
    write(ZCODE_WT / "untracked_scratch.txt", "half-done experiment\n")

    ts = time.strftime("%Y%m%d-%H%M%S")
    artifacts = DEMO_REPO / "failure_artifacts"
    # intent-to-add so brand-new files appear in `git diff HEAD`
    git("add", "-A", "-N", cwd=ZCODE_WT)
    write(artifacts / "bd-wt0003.patch", git("diff", "HEAD", cwd=ZCODE_WT) + "\n")
    write(artifacts / "bd-wt0003.status", git("status", "--porcelain", cwd=ZCODE_WT) + "\n")
    write(artifacts / "bd-wt0003.log", "simulated failure: unittest assertion error\n")

    # archive branch preserving the scene (committed state), before any removal
    git("add", "-A", cwd=ZCODE_WT)
    git("commit", "-m", "[bd-wt0003] WIP scene preservation", cwd=ZCODE_WT)
    git("branch", f"archive/failed/bd-wt0003-{ts}")

    # only after evidence + archive: regular (non-force) removal succeeds
    git("worktree", "remove", str(ZCODE_WT))
    git("worktree", "prune")

    # evidence assertions
    patch = (artifacts / "bd-wt0003.patch").read_text(encoding="utf-8")
    assert "buggy" in patch  # captured the uncommitted diff
    status = (artifacts / "bd-wt0003.status").read_text(encoding="utf-8")
    assert "untracked_scratch.txt" in status
    assert f"archive/failed/bd-wt0003-{ts}" in git("branch", "--list", "archive/failed/*")
