"""CodexCLIInvoker — minimal shared transport for Codex CLI calls (P2-03 §10).

One read-only, ephemeral, tool-less `codex exec` per call:
    codex exec -s read-only --ephemeral --skip-git-repo-check --ignore-rules
               -c model_reasoning_effort="<effort>"
               --output-schema <schema.json> -o <last-message.json> -
Full prompt on stdin, neutral cwd (no repo access), final message parsed from
the -o file. This is deliberately NOT a general LLM framework — Planner and
the Decision engines keep their own prompt/schema/domain logic and only share
this transport. Sanitized evidence only; no keys, no headers, no CoT.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

_TOKENS_RE = re.compile(r"tokens used\s+([\d,]+)", re.IGNORECASE)


class CodexInvokeError(RuntimeError):
    """One codex call failed (transport/refusal/timeout/malformed)."""


class CodexCLIInvoker:
    def __init__(self, *, effort: str | None = None, timeout: int | None = None) -> None:
        self.effort = effort or os.environ.get("AEO_CODEX_REASONING_EFFORT", "high")
        self.timeout = timeout or int(os.environ.get("AEO_CODEX_TIMEOUT_SECONDS", "300"))

    def _bin(self) -> str:
        from shutil import which
        exe = which("codex")
        if not exe:
            raise CodexInvokeError("codex CLI not found on PATH")
        return exe

    def invoke(self, *, system_prompt: str, user_content: str,
               schema: dict, schema_name: str = "structured_output") -> dict:
        """Returns {payload, usage:{total_tokens}, meta:{model, reasoning_effort}}."""
        full_prompt = system_prompt + "\n\n---\n" + user_content
        with tempfile.TemporaryDirectory(prefix="aeo-codex-") as tmp:
            tmp_path = Path(tmp)
            schema_file = tmp_path / f"{schema_name}.json"
            schema_file.write_text(json.dumps(schema), encoding="utf-8")
            last_file = tmp_path / "last-message.json"
            try:
                proc = subprocess.run(
                    [self._bin(), "exec",
                     "-s", "read-only", "--ephemeral", "--skip-git-repo-check",
                     "--ignore-rules",
                     "-c", f'model_reasoning_effort="{self.effort}"',
                     "--output-schema", str(schema_file),
                     "-o", str(last_file),
                     "-"],
                    input=full_prompt, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=self.timeout,
                    cwd=str(Path(tempfile.gettempdir())),
                )
            except subprocess.TimeoutExpired as exc:
                raise CodexInvokeError("codex exec timed out") from exc
            if proc.returncode != 0:
                raise CodexInvokeError(
                    f"codex exec rc={proc.returncode}: {(proc.stderr or proc.stdout)[-300:]}")
            if not last_file.exists():
                raise CodexInvokeError("codex exec produced no final message file")
            text = last_file.read_text(encoding="utf-8", errors="replace").strip()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CodexInvokeError(f"final message not JSON: {text[:200]}") from exc
            match = _TOKENS_RE.search(proc.stdout or "")
            model = ""
            for line in (proc.stdout or "").splitlines():
                if line.lower().startswith("model:"):
                    model = line.split(":", 1)[1].strip()
                    break
        return {"payload": payload,
                "usage": {"total_tokens": int(match.group(1).replace(",", "")) if match else None},
                "meta": {"model": model or "codex-default(chatgpt)",
                         "reasoning_effort": self.effort}}
