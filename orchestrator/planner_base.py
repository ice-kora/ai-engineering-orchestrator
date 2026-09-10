"""Shared LLM-planner orchestration (backend-agnostic).

Owns everything that must be IDENTICAL regardless of which model backend is
used (P2-02 §13: planners may differ only in UserRequest->ExecutionPlan):

  attempt loop (max total calls = 2)
    -> backend _invoke() (returns {draft, usage, backend_meta})
    -> _validate_draft()  [host-authoritative assembly + Draft 2020-12
                           contract + DAG + runtime policy]
    -> success: return (plan, evidence)  — caller saves PLANNED as LAST step
    -> repairable failure: one retry whose prompt carries the violations
    -> otherwise/second failure: GPTPlannerError, PLAN_NOT_SAVED, nothing saved

This module is import-root (no imports from sibling planner modules) to avoid
circular imports; gpt_planner re-exports the shared names for compatibility.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator import contracts, policy

REPAIRABLE = ("schema", "contract", "dag", "policy", "parse")


class GPTPlannerError(RuntimeError):
    """Plan could not be safely produced. NOTHING was saved."""

    def __init__(self, message: str, *, error_category: str, evidence: dict | None = None):
        super().__init__(message)
        self.error_category = error_category
        self.evidence = evidence or {}


class ApiKeyMissing(GPTPlannerError):
    def __init__(self, message: str = "OPENAI_API_KEY not set; refusing to guess") -> None:
        super().__init__(message, error_category="api_key_missing")


# ---- repo grounding (shared by all backends) ---------------------------------

MAX_CONTEXT_CHARS = 24000          # hard repo-context budget
MAX_TRACKED_FILES_LISTED = 600
MAX_README_CHARS = 4000


@dataclass
class RepoContext:
    target_repo: str
    head: str
    tracked_files: list[str] = field(default_factory=list)
    readme_excerpt: str = ""

    def render(self) -> str:
        files = "\n".join(self.tracked_files)
        readme = self.readme_excerpt or "(none)"
        return (f"target_repo: {self.target_repo}\ncurrent HEAD: {self.head}\n\n"
                f"README (excerpt):\n{readme}\n\ntracked files:\n{files}")

    @classmethod
    def build(cls, repo: Path) -> "RepoContext":
        import subprocess

        def git(*args: str) -> str:
            proc = subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace")
            return proc.stdout.strip() if proc.returncode == 0 else ""

        head = git("rev-parse", "HEAD")[:12] or "(unknown)"
        files = [f.replace("\\", "/") for f in git("ls-files").splitlines() if f.strip()]
        readme = ""
        for name in ("README.md", "readme.md", "README"):
            p = repo / name
            if p.exists():
                readme = p.read_text(encoding="utf-8", errors="replace")[:MAX_README_CHARS]
                break
        ctx = cls(target_repo=str(repo), head=head, tracked_files=files[:MAX_TRACKED_FILES_LISTED],
                  readme_excerpt=readme)
        # hard char budget (defense in depth beyond the file-count cap)
        while len(ctx.render()) > MAX_CONTEXT_CHARS and len(ctx.tracked_files) > 50:
            ctx.tracked_files = ctx.tracked_files[: len(ctx.tracked_files) // 2]
        return ctx

    def snapshot(self) -> policy.RepoSnapshot:
        dirs = {"/".join(f.split("/")[:-1]) for f in self.tracked_files if "/" in f}
        return policy.RepoSnapshot(tracked_files=set(self.tracked_files), tracked_dirs=dirs)


# ---- evidence -----------------------------------------------------------------

@dataclass
class PlanEvidence:
    """Non-sensitive planner metadata (key/headers/CoT are NEVER recorded)."""
    request_id: str
    plan_id: str
    backend: str = ""
    model: str = ""
    reasoning_effort: str = ""
    prompt_version: str = ""
    attempts: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"request_id": self.request_id, "plan_id": self.plan_id,
                "backend": self.backend, "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "prompt_version": self.prompt_version,
                "attempts": self.attempts}


class _BaseLLMPlanner:
    backend: str = "?"
    prompt_version: str = "?"

    def __init__(self, repo: Path, *, plan_id: str | None = None,
                 evidence_dir: Path | None = None) -> None:
        self.repo = Path(repo)
        self.plan_id = plan_id or f"plan-{self.backend}-{uuid.uuid4().hex[:8]}"
        self.evidence_dir = evidence_dir
        self.max_calls = 2
        self.model = ""
        self.effort = ""
        self._request_dict: dict = {}

    # ---- hooks a backend must provide ----

    def _client_or_raise(self):
        """Return a backend client/handle or raise (e.g. ApiKeyMissing)."""
        raise NotImplementedError

    def _invoke(self, client, prompt_text: str, repo_ctx: RepoContext,
                repair_errors: list[str] | None) -> dict:
        """One model call. Returns {draft: dict, usage: dict, backend_meta: dict}.
        Raises on transport/refusal/timeout (counted against the call budget)."""
        raise NotImplementedError

    # ---- shared validation pipeline ----

    def _validate_draft(self, draft: dict, request, repo_ctx: RepoContext):
        """Returns (plan, errors); plan is None iff errors non-empty."""
        full = {
            "plan_id": self.plan_id,                 # host-authoritative
            "request_id": request.request_id,        # host-authoritative
            "target_repo": request.target_repo,      # host-authoritative
            "requires_human_approval": True,         # host-authoritative
            "summary": draft.get("summary", ""),
            "tasks": draft.get("tasks", []),
            "risks": draft.get("risks", []),
            "assumptions": draft.get("assumptions", []),
        }
        try:
            plan = contracts.ExecutionPlan.from_dict(full)       # local Draft 2020-12
            plan.validate_dag()                                   # semantic DAG
        except contracts.ContractError as exc:
            return None, ["contract: " + str(exc)]
        policy_errors = policy.validate(full["tasks"], repo_ctx.target_repo, repo_ctx.snapshot())
        if policy_errors:
            return None, ["policy: " + e for e in policy_errors]
        return plan, []

    def _prompt_text(self) -> str:
        return (Path(__file__).resolve().parents[1] / "prompts"
                / f"{self.prompt_version}.md").read_text(encoding="utf-8")

    # ---- shared plan() orchestration (bounded retry, fail closed) ----

    def plan(self, request):
        prompt_text = self._prompt_text()
        repo_ctx = RepoContext.build(self.repo)
        self._request_dict = request.to_dict()

        evidence = PlanEvidence(request_id=request.request_id, plan_id=self.plan_id,
                                backend=self.backend, prompt_version=self.prompt_version)
        client = self._client_or_raise()  # ApiKeyMissing etc: fail before any call

        repair_errors: list[str] | None = None
        calls_made = 0
        last_category = "unknown"
        try:
            for _attempt in (1, 2):
                if calls_made >= self.max_calls:
                    break
                t0 = time.monotonic()
                try:
                    result = self._invoke(client, prompt_text, repo_ctx, repair_errors)
                except GPTPlannerError:
                    raise
                except Exception as exc:  # timeout / refusal / malformed / transport
                    calls_made += 1
                    last_category = "api_error"
                    evidence.attempts.append({
                        "attempt": calls_made,
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                        "validation_result": "api_error",
                        "error_category": "api_error",
                        "error_brief": type(exc).__name__,
                    })
                    continue  # a retry consumes the same global budget of 2
                calls_made += 1
                record = {"attempt": calls_made,
                          "latency_ms": int((time.monotonic() - t0) * 1000),
                          **result.get("backend_meta", {}), **(result.get("usage") or {})}
                if not evidence.model and result.get("backend_meta", {}).get("model"):
                    evidence.model = result["backend_meta"]["model"]
                if not evidence.reasoning_effort and result.get("backend_meta", {}).get("reasoning_effort"):
                    evidence.reasoning_effort = result["backend_meta"]["reasoning_effort"]

                plan, errors = self._validate_draft(result["draft"], request, repo_ctx)
                if plan is not None:
                    record["validation_result"] = "valid"
                    evidence.attempts.append(record)
                    return plan, evidence

                category = errors[0].split(":", 1)[0]
                last_category = category if category in REPAIRABLE else "unrepairable"
                record["validation_result"] = "invalid"
                record["error_category"] = last_category
                record["violations"] = errors[:12]
                evidence.attempts.append(record)
                if last_category == "unrepairable" or calls_made >= self.max_calls:
                    break
                repair_errors = errors
        finally:
            self._record_evidence(evidence)

        raise GPTPlannerError(
            f"plan not saved: {last_category} violations persisted after {calls_made} call(s); "
            "PLAN_NOT_SAVED / APPROVAL_NOT_AVAILABLE / MATERIALIZATION_FORBIDDEN",
            error_category=last_category, evidence=evidence.summary())

    # -- evidence persistence (gitignored dir; sanitized summary only) --

    def _record_evidence(self, evidence: PlanEvidence) -> None:
        if not self.evidence_dir:
            return
        try:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            path = self.evidence_dir / f"{evidence.plan_id}.json"
            path.write_text(json.dumps(evidence.summary(), ensure_ascii=False, indent=1),
                            encoding="utf-8")
        except OSError:
            pass  # evidence is best-effort; never blocks the flow
