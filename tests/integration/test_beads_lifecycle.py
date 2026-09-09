"""P0-09: Beads lifecycle + atomic-claim concurrency (runs when `bd` is installed).

Covers the v1.0 §六.A checklist (create/dep/ready/claim/update/close), the
GPT gate requirement "原子 Claim 必须做真实并发测试", and the v1.1 §8.3
spec-vs-reality verification (`bd update --status open`, `bd label` subcommands
are UNVERIFIED in official docs — if absent, record evidence for GPT, do not
work around by touching the underlying DB).

SKIP-BLOCKED while bd is not installed.
"""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_REPO = PROJECT_ROOT / "sandbox" / "demo-repo"
BD = shutil.which("bd")

pytestmark = pytest.mark.skipif(
    BD is None,
    reason="BLOCKED: bd not installed (docs/INSTALLATION-PROPOSAL.md §1 awaiting approval)",
)


def bd(*args: str, cwd: Path = DEMO_REPO, check: bool = True) -> subprocess.CompletedProcess:
    # NOTE (spec-vs-reality): bd -C exists but refuses dirs without an existing
    # beads project ("no beads project found") — unusable for `init`. Process cwd
    # works for every command including init, so we rely on cwd only.
    proc = subprocess.run(
        [BD, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd),
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"bd {args} failed:\n{proc.stderr}")
    return proc


@pytest.fixture(scope="module")
def beads_repo(tmp_path_factory):
    """A disposable beads project (embedded mode) inside a temp git repo."""
    repo = tmp_path_factory.mktemp("beads-") / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo),
                   capture_output=True, text=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "aeo-dev"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "aeo-dev@local"],
                   capture_output=True, check=True)
    proc = bd("init", cwd=repo, check=False)
    assert proc.returncode == 0, f"bd init failed:\n{proc.stderr}"
    return repo


def _task_id(output: str) -> str:
    # Real format (v1.2.2): "<repo-prefix>-<4 rand>" e.g. "repo-qgz"; prefix is
    # per-repo configurable (bd rename-prefix). Parse from the creation line.
    import re
    m = re.search(r"Created issue: ([A-Za-z0-9][A-Za-z0-9-]*-[a-z0-9]+)", output)
    assert m, f"no task id in output: {output!r}"
    return m.group(1)


def test_create_dependency_ready(beads_repo):
    parent = _task_id(bd("create", "P0 impl task", "-p", "1", cwd=beads_repo).stdout)
    child = _task_id(bd("create", "P0 review task", "-p", "1", cwd=beads_repo).stdout)
    bd("dep", "add", child, parent, cwd=beads_repo)

    ready = bd("ready", "--json", cwd=beads_repo).stdout
    ids = [t.get("id") for t in json.loads(ready)] if ready.strip().startswith(("[", "{")) else ready
    assert parent in str(ids)
    assert child not in str(ids), "blocked child must not be in ready queue"


def test_atomic_claim_concurrency(beads_repo):
    """GPT gate: real concurrent claim — exactly one winner."""
    task = _task_id(bd("create", "P0 concurrency probe", "-p", "0", cwd=beads_repo).stdout)

    def claim(agent: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BD, "update", task, "--claim"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(beads_repo), env={"BEADS_ACTOR": agent, **__import__("os").environ},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["agent-zcode", "agent-antigravity"]))
    winners = [r for r in results if r.returncode == 0]
    assert len(winners) == 1, (
        f"atomic claim violated: {len(winners)} winners\n"
        + "\n".join(f"rc={r.returncode} out={r.stdout[-200:]} err={r.stderr[-200:]}" for r in results)
    )


def test_spec_vs_reality_compensation_commands(beads_repo):
    """v1.1 §8.3 verification: `update --status open` and `label` subcommands.

    These are documented in architecture_spec_v1.1 but NOT confirmed by upstream
    docs. If they fail here, that is recorded EVIDENCE for GPT arbitration —
    the test reports (xfail) rather than pretending support.
    """
    task = _task_id(bd("create", "P0 compensation probe", "-p", "0", cwd=beads_repo).stdout)
    bd("update", task, "--claim", cwd=beads_repo)

    status_open = bd("update", task, "--status", "open", cwd=beads_repo, check=False)
    label = bd("label", "add", task, "stage:READY", cwd=beads_repo, check=False)

    if status_open.returncode != 0 or label.returncode != 0:
        pytest.xfail(
            "EVIDENCE for GPT: v1.1 §8.3 compensation commands unavailable in this bd "
            f"build (update --status open rc={status_open.returncode}: {status_open.stderr.strip()[:200]}; "
            f"label rc={label.returncode}: {label.stderr.strip()[:200]})"
        )


def test_close(beads_repo):
    task = _task_id(bd("create", "P0 close probe", "-p", "2", cwd=beads_repo).stdout)
    bd("update", task, "--claim", cwd=beads_repo)
    # real syntax: message goes via --reason, not positional (spec-vs-reality)
    bd("close", task, "--reason", "done: P0 probe", cwd=beads_repo)
