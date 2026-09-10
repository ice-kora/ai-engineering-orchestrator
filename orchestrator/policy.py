"""P2-02 runtime policy / hallucination guard (local, deterministic).

Runs AFTER OpenAI structured output + Draft 2020-12 schema + DAG validation
and BEFORE store.save_plan(..., PLANNED). A plan that fails any rule here must
never become PLANNED (failure atomicity).
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_TASKS = 8
MIN_TASKS = 1

# V1 execution reality (P2-02 §7): the only runnable role pair.
REQUIRED_EXECUTOR = "zcode"
REQUIRED_REVIEWER = "antigravity"

# Paths inside the target repo that agents must never plan to touch.
FORBIDDEN_PREFIXES = (".git/", ".beads/")
FORBIDDEN_EXACT = {".git", ".beads"}

# Orchestrator/host-owned locations that must never appear as target paths.
FORBIDDEN_MARKERS = ("orchestrator-state", "agent-mail-test-data", "zcode-headless-scratch",
                     "planner-evidence", "failure_artifacts")


@dataclass
class RepoSnapshot:
    """Minimal, read-only repo grounding (built by gpt_planner, reused here)."""
    tracked_files: set[str]
    tracked_dirs: set[str]


def validate(plan_draft_tasks: list[dict], repo_root_name: str, snap: RepoSnapshot) -> list[str]:
    """Return a list of policy violations ([] = clean).

    plan_draft_tasks: list of PlannedTask-shaped dicts (task_key/target_paths/
    dependencies/executor_role/reviewer_role at minimum).
    """
    errors: list[str] = []

    n = len(plan_draft_tasks)
    if not (MIN_TASKS <= n <= MAX_TASKS):
        errors.append(f"task count {n} outside {MIN_TASKS}..{MAX_TASKS}")

    keys = [t.get("task_key", "") for t in plan_draft_tasks]
    if len(set(keys)) != len(keys):
        errors.append("duplicate task_key")

    for t in plan_draft_tasks:
        key = t.get("task_key", "<missing>")
        if t.get("executor_role") != REQUIRED_EXECUTOR:
            errors.append(f"{key}: executor_role must be '{REQUIRED_EXECUTOR}' (got {t.get('executor_role')!r})")
        if t.get("reviewer_role") != REQUIRED_REVIEWER:
            errors.append(f"{key}: reviewer_role must be '{REQUIRED_REVIEWER}' (got {t.get('reviewer_role')!r})")

        paths = t.get("target_paths") or []
        if not paths:
            errors.append(f"{key}: target_paths empty")
        for p in paths:
            errors.extend(_path_errors(p, key, snap))
    return errors


def _path_errors(path: str, key: str, snap: RepoSnapshot) -> list[str]:
    if not isinstance(path, str) or not path.strip():
        return [f"{key}: empty target_path"]
    p = path.replace("\\", "/").strip()
    errs: list[str] = []
    if ":" in p.split("/")[0] and len(p.split("/")[0]) <= 2:      # windows drive
        errs.append(f"{key}: absolute path not allowed: {path}")
    if p.startswith("/") or p.startswith("\\"):
        errs.append(f"{key}: absolute path not allowed: {path}")
    if ".." in p.split("/"):
        errs.append(f"{key}: path escape ('..') not allowed: {path}")
    first = p.split("/")[0]
    if first in FORBIDDEN_EXACT or p.startswith(FORBIDDEN_PREFIXES):
        errs.append(f"{key}: forbidden repo-internal path: {path}")
    for marker in FORBIDDEN_MARKERS:
        if marker in p:
            errs.append(f"{key}: path points at orchestrator-managed state: {path}")
    if not errs:
        if p in snap.tracked_files:
            return []                                               # existing file: ok
        parent = "/".join(p.split("/")[:-1])
        if parent and (parent in snap.tracked_dirs or parent in ("src", "tests")):
            return []                                               # new file under known dir
        errs.append(f"{key}: path not grounded in RepositoryContext (unknown file/dir): {path}")
    return errs
