"""P2-02 GPTPlanner — OpenAI Responses API backend (gpt-5.6-sol).

Shared planner orchestration (validation pipeline, bounded retry, evidence,
failure atomicity) lives in ``planner_base``; this module provides the
Responses-API ``_invoke`` backend plus the plan-draft wire schema.

Backend reality note (runtime errata E-09): the GPT instruction assumed an
OpenAI API key, but the user runs a ChatGPT subscription (no API key). The
Codex-CLI backend (``orchestrator/codex_planner.py``) is the subscription-
compatible implementation; this API backend is retained and fully functional
but marked NOT_AVAILABLE until an OPENAI_API_KEY exists.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from orchestrator.planner_base import (  # noqa: F401  (re-exports for compat)
    ApiKeyMissing, GPTPlannerError, RepoContext, _BaseLLMPlanner,
)

PROMPT_VERSION = "gpt-planner-v1"

DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_CALLS = 2


# ---- OpenAI strict-JSON-schema subset for the PLAN DRAFT --------------------
# NOTE: deliberately narrower than EXECUTION_PLAN_SCHEMA — both the OpenAI
# strict Structured Outputs subset and codex --output-schema accept only a
# Draft-2020-12 subset (no pattern, all additionalProperties:false, every
# property required). Local Draft-2020-12 validation of the assembled plan
# still runs afterwards; the wire schema never replaces the local contract.

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


class GPTPlanner(_BaseLLMPlanner):
    """Real planner via OpenAI Responses API (requires OPENAI_API_KEY)."""

    backend = "openai-responses"
    prompt_version = PROMPT_VERSION

    def __init__(self, repo: Path, *, plan_id: str | None = None,
                 evidence_dir: Path | None = None) -> None:
        super().__init__(repo, plan_id=plan_id, evidence_dir=evidence_dir)
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

    def _make_client(self):
        key = self._load_api_key()
        if not key:
            raise ApiKeyMissing()
        from openai import OpenAI
        return OpenAI(api_key=key, timeout=self.timeout)

    def _client_or_raise(self):
        return self._make_client()  # raises ApiKeyMissing (fail closed)

    def _invoke(self, client, prompt_text: str, repo_ctx: RepoContext,
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
        return {
            "draft": json.loads(resp.output_text),   # SDK guarantees schema-conformant JSON
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            } if usage else {"total_tokens": None},
            "backend_meta": {"response_id": getattr(resp, "id", ""),
                             "model": self.model, "reasoning_effort": self.effort},
        }
