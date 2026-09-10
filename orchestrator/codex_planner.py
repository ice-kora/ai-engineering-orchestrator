"""CodexCLIPlanner — ChatGPT-subscription backend via the Codex CLI (P2-02).

Reality (errata E-09): the user has a ChatGPT subscription and no OpenAI API
key; `codex` CLI ships with subscription auth already logged in. This backend
invokes ONE read-only, ephemeral, tool-less codex exec call per attempt:

    codex exec -s read-only --ephemeral --skip-git-repo-check --ignore-rules
               -c model_reasoning_effort="high"
               --output-schema <plan-draft.json> -o <last-message.txt> -

with the full prompt on stdin (bounded by the same RepoContext budget) and
`-` reading it. The final agent message is required to conform to the same
plan-draft schema wire subset; the local four-gate pipeline in planner_base
(host-authoritative assembly -> Draft 2020-12 -> DAG -> runtime policy)
remains the ONLY authority for saving.

Codex sandbox note: read-only keeps the planner from touching any workspace;
we do NOT pass -C (no repo browsing: grounding comes solely from the pinned
RepoContext snapshot, matching the API backend's determinism).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from orchestrator.gpt_planner import GPT_PLAN_DRAFT_SCHEMA, PROMPT_VERSION
from orchestrator.planner_base import RepoContext, _BaseLLMPlanner

DEFAULT_TIMEOUT = 300          # codex high-effort runs are slower than raw API
DEFAULT_EFFORT = "high"
DEFAULT_MAX_CALLS = 2

_TOKENS_RE = re.compile(r"tokens used\s+([\d,]+)", re.IGNORECASE)


class CodexNotInstalled(RuntimeError):
    pass


class CodexCLIPlanner(_BaseLLMPlanner):
    backend = "codex-cli"
    prompt_version = PROMPT_VERSION

    def __init__(self, repo: Path, *, plan_id: str | None = None,
                 evidence_dir: Path | None = None) -> None:
        super().__init__(repo, plan_id=plan_id, evidence_dir=evidence_dir)
        self.effort = os.environ.get("AEO_CODEX_REASONING_EFFORT", DEFAULT_EFFORT)
        self.timeout = int(os.environ.get("AEO_CODEX_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
        self.max_calls = int(os.environ.get("AEO_CODEX_MAX_ATTEMPTS", str(DEFAULT_MAX_CALLS)))
        self.model = ""  # filled from the run's stdout header if present

    # ---- backend hooks ----

    def _codex_bin(self) -> str:
        from shutil import which
        exe = which("codex")
        if exe:
            return exe
        raise CodexNotInstalled(
            "codex CLI not found on PATH (npm i -g @openai/codex + `codex login`)")

    def _client_or_raise(self):
        # fail fast on missing binary; login state is validated by the run itself
        self._codex_bin()
        return True  # stateless: no persistent client

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
        full_prompt = prompt_text + "\n\n---\n" + user_content

        with tempfile.TemporaryDirectory(prefix="aeo-codex-") as tmp:
            tmp_path = Path(tmp)
            schema_file = tmp_path / "plan-draft-schema.json"
            schema_file.write_text(json.dumps(GPT_PLAN_DRAFT_SCHEMA["schema"]),
                                   encoding="utf-8")
            last_file = tmp_path / "last-message.json"
            proc = subprocess.run(
                [self._codex_bin(), "exec",
                 "-s", "read-only", "--ephemeral", "--skip-git-repo-check",
                 "--ignore-rules",
                 "-c", f'model_reasoning_effort="{self.effort}"',
                 "--output-schema", str(schema_file),
                 "-o", str(last_file),
                 "-"],
                input=full_prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.timeout,
                cwd=str(Path(tempfile.gettempdir())),  # neutral cwd, no repo access
            )
            if proc.returncode != 0:
                raise RuntimeError(f"codex exec rc={proc.returncode}: "
                                   f"{(proc.stderr or proc.stdout)[-400:]}")
            if not last_file.exists():
                raise RuntimeError("codex exec produced no final message file")
            draft_text = last_file.read_text(encoding="utf-8", errors="replace").strip()
            try:
                draft = json.loads(draft_text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"final message not JSON: {draft_text[:200]}") from exc

            match = _TOKENS_RE.search(proc.stdout or "")
            total = int(match.group(1).replace(",", "")) if match else None
            model_header = ""
            for line in (proc.stdout or "").splitlines():
                if line.lower().startswith("model:"):
                    model_header = line.split(":", 1)[1].strip()
                    break

        return {
            "draft": draft,
            "usage": {"total_tokens": total},
            "backend_meta": {"model": model_header or "codex-default(chatgpt)",
                             "reasoning_effort": self.effort,
                             "backend": self.backend},
        }
