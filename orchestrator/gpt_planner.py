"""P2-02 GPTPlanner — real OpenAI Responses API planner (gpt-5.6-sol).

Pipeline (fail-closed at every stage; save_plan(PLANNED) is the LAST step):

  UserRequest + RepoContext
    -> prompts/gpt-planner-v1.md (versioned)
    -> Responses API (store=false, NO tools, text.format=json_schema strict)
    -> API schema validation (SDK-enforced structured output)
    -> host-authoritative assembly (request_id/plan_id/target_repo/approval
       are LOCAL — the model never sees nor decides them)
    -> ExecutionPlan.from_dict (local Draft 2020-12 full contract)
    -> validate_dag (unknown deps / duplicates / cycles)
    -> policy.validate (roles/path grounding/task bounds)
    -> (all green) Store.save_plan(..., PLANNED)

Retry: MAX 2 total API calls. A second call happens only for repairable
semantic violations and must regenerate the COMPLETE plan with the previous
errors appended; code never silently patches the model's output. Two strikes
=> GPTPlannerError, PLAN_NOT_SAVED, nothing approvable.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator import contracts, policy

PROMPT_VERSION = "gpt-planner-v1"

DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_CALLS = 2

MAX_CONTEXT_CHARS = 24000          # hard repo-context budget
MAX_TRACKED_FILES_LISTED = 600
MAX_README_CHARS = 4000

# Repairable categories (a regenerate-with-errors retry may fix these)
REPAIRABLE = ("schema", "contract", "dag", "policy", "parse")


class GPTPlannerError(RuntimeError):
    """Plan could not be safely produced. NOTHING was saved."""

    def __init__(self, message: str, *, error_category: str, evidence: dict | None = None):
        super().__init__(message)
        self.error_category = error_category
        self.evidence = evidence or {}


class ApiKeyMissing(GPTPlannerError):
    def __init__(self) -> None:
        super().__init__("OPENAI_API_KEY not set (.env or environment); refusing to guess",
                         error_category="api_key_missing")


# ---- OpenAI strict-JSON-schema subset for the PLAN DRAFT --------------------
# NOTE: deliberately narrower than EXECUTION_PLAN_SCHEMA — OpenAI Structured
# Outputs supports only a subset of Draft 2020-12 (no pattern here, all
# additionalProperties:false, every property required). Local Draft-2020-12
# validation of the assembled plan still runs afterwards; the API schema does
# NOT replace the local contract.

GPT_PLAN_DRAFT_SCHEMA = {
    "name": "execution_plan_draft",
    "type": "json_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "tasks", "risks", "assumptions"],
        "properties": {
            "summary": {"type": "string"},
            "tasks": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_key", "title", "description", "target_paths",
                                 "acceptance_criteria", "dependencies",
                                 "executor_role", "reviewer_role", "risk_level"],
                    "properties": {
                        "task_key": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "target_paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "acceptance_criteria": {"type": "string"},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "executor_role": {"type": "string", "enum": ["zcode"]},
                        "reviewer_role": {"type": "string", "enum": ["antigravity"]},
                        "risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                    },
                },
            },
            "risks": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
        },
    },
}


# ---- repo grounding ----------------------------------------------------------

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


# ---- planner ------------------------------------------------------------------

@dataclass
class PlanEvidence:
    """Non-sensitive planner metadata (key/headers/CoT are NEVER recorded)."""
    request_id: str
    plan_id: str
    model: str
    reasoning_effort: str
    prompt_version: str
    attempts: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"request_id": self.request_id, "plan_id": self.plan_id,
                "model": self.model, "reasoning_effort": self.reasoning_effort,
                "prompt_version": self.prompt_version,
                "attempts": self.attempts}


class GPTPlanner:
    """Real planner (P2-02). Shares the downstream pipeline with JsonPlanner."""

    def __init__(self, repo: Path, *, plan_id: str | None = None,
                 evidence_dir: Path | None = None) -> None:
        self.repo = Path(repo)
        self.plan_id = plan_id or f"plan-gpt-{uuid.uuid4().hex[:8]}"
        self.evidence_dir = evidence_dir  # None => caller manages recording
        self.model = os.environ.get("AEO_OPENAI_MODEL", DEFAULT_MODEL)
        self.effort = os.environ.get("AEO_OPENAI_REASONING_EFFORT", DEFAULT_EFFORT)
        self.timeout = int(os.environ.get("AEO_OPENAI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
        self.max_calls = int(os.environ.get("AEO_OPENAI_MAX_ATTEMPTS", str(DEFAULT_MAX_CALLS)))

    # -- key loading (env first, then project .env; never logged) --

    @staticmethod
    def _load_api_key() -> str:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if key:
            return key
        env_file = Path(__file__).resolve().parents[1] / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    candidate = line.split("=", 1)[1].strip()
                    if candidate:
                        return candidate
        return ""

    # -- client (lazy; injectable for tests) --

    def _make_client(self):
        key = self._load_api_key()
        if not key:
            raise ApiKeyMissing()
        from openai import OpenAI
        return OpenAI(api_key=key, timeout=self.timeout)

    # -- one API call --

    def _call_api(self, client, prompt_text: str, repo_ctx: RepoContext,
                  repair_errors: list[str] | None) -> dict:
        user_content = (
            f"UserRequest:\n{json.dumps(self._request_dict, ensure_ascii=False, indent=1)}\n\n"
            f"RepositoryContext:\n{repo_ctx.render()}"
        )
        if repair_errors:
            user_content += ("\n\nYour previous plan draft was REJECTED by local validation:\n- "
                             + "\n- ".join(repair_errors)
                             + "\n\nRegenerate a COMPLETE corrected plan (all tasks, not a patch).")
        resp = client.responses.create(
            model=self.model,
            input=[{"role": "system", "content": prompt_text},
                   {"role": "user", "content": user_content}],
            reasoning={"effort": self.effort},
            store=False,
            text={"format": GPT_PLAN_DRAFT_SCHEMA},
        )
        usage = getattr(resp, "usage", None)
        text_out = resp.output_text
        return {
            "response_id": getattr(resp, "id", ""),
            "draft": json.loads(text_out),       # SDK guarantees schema-conformant JSON
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            } if usage else {},
        }

    # -- validation of one draft -> assembled ExecutionPlan --

    def _validate_draft(self, draft: dict, request, repo_ctx: RepoContext):
        """Returns (plan, errors). plan is None iff errors non-empty."""
        # host-authoritative fields — model output NEVER touches these
        full = {
            "plan_id": self.plan_id,
            "request_id": request.request_id,
            "target_repo": request.target_repo,
            "requires_human_approval": True,
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

    # -- public API (PlannerPort) --

    _request_dict: dict = {}  # set inside plan() (kept private)

    def plan(self, request) -> "tuple":
        """Returns (ExecutionPlan, PlanEvidence). Raises GPTPlannerError on failure.

        NOTE: raises instead of saving anything — failure atomicity: an invalid
        model output must never become PLANNED.
        """
        from pathlib import Path as _P
        prompt_text = (_P(__file__).resolve().parents[1] / "prompts" / f"{PROMPT_VERSION}.md") \
            .read_text(encoding="utf-8")
        repo_ctx = RepoContext.build(self.repo)
        self._request_dict = request.to_dict()

        evidence = PlanEvidence(request_id=request.request_id, plan_id=self.plan_id,
                                model=self.model, reasoning_effort=self.effort,
                                prompt_version=PROMPT_VERSION)
        client = self._make_client()  # may raise ApiKeyMissing (fail closed)

        repair_errors: list[str] | None = None
        calls_made = 0
        last_category = "unknown"
        try:
            for attempt in (1, 2):
                if calls_made >= self.max_calls:
                    break
                t0 = time.monotonic()
                try:
                    result = self._call_api(client, prompt_text, repo_ctx, repair_errors)
                except ApiKeyMissing:
                    raise
                except Exception as exc:  # timeout / refusal / malformed / transport
                    calls_made += 1
                    last_category = "api_error"
                    evidence.attempts.append({
                        "attempt": calls_made, "latency_ms": int((time.monotonic() - t0) * 1000),
                        "validation_result": "api_error",
                        "error_category": "api_error", "error_brief": type(exc).__name__,
                        **({"response_id": result.get("response_id")} if 'result' in dir() else {}),
                    })
                    continue  # a retry consumes the same global budget of 2
                calls_made += 1
                record = {
                    "attempt": calls_made,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "response_id": result["response_id"],
                    **result["usage"],
                }

                plan, errors = self._validate_draft(result["draft"], request, repo_ctx)
                if plan is not None:
                    record["validation_result"] = "valid"
                    evidence.attempts.append(record)
                    self._record_evidence(evidence)
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
