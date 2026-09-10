"""AntigravityAdapter — independent cross-review via verified `agy` headless.

P0 constraint baked in: agy's git-root inference inside worktrees is unreliable
(antigravity-cli#68 two variants), so we ALWAYS pass an absolute working
directory and verify the worktree is not dirtied by the reviewer afterwards.
The verdict is machine-authoritative via --json-schema structured output.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from adapters import handover
from adapters.config import PATHS

REVIEW_SYSTEM_PROMPT = """You are an INDEPENDENT code reviewer (you did NOT write this code).
Review the change below against the acceptance criteria. Be strict but fair.

TASK_ID: {task_id}
BRANCH: {branch} (worktree: {worktree})
BASE..HEAD: {base}..{head}
ACCEPTANCE CRITERIA (authoritative):
{acceptance}

HANDOVER PAYLOAD (machine-authoritative context):
{handover_json}

FULL DIFF (base..head):
{diff}

Reviewer rules:
- Focus on whether the acceptance criteria are fully satisfied and tests prove it.
- LOGIC_BUG: criteria violated by the implementation; TEST_MISSING: criterion untested.
- verified_head_commit MUST equal the head commit above.
- verdict APPROVED only if every criterion is implemented AND covered by passing tests.
"""


class AntigravityError(RuntimeError):
    pass


class AntigravityAdapter:
    def __init__(self, worktree: Path, timeout: float = 300) -> None:
        self.worktree = Path(worktree).resolve()  # absolute only (#68)
        self.timeout = timeout

    def _git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(self.worktree), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise AntigravityError(f"git {args} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def review(self, task_id: str, handover_payload: dict, acceptance: str,
               base: str = "main") -> dict:
        """Run one review iteration; returns a validated ReviewReportPayload."""
        head = handover_payload["git_context"]["head_commit"]
        branch = handover_payload["git_context"]["branch_name"]
        diff = self._git("diff", f"{base}..{head}", "--", "*")
        if not diff:
            diff = self._git("diff", base, head)

        dirty_before = self._git("status", "--porcelain")

        schema = json.dumps(_agy_review_output_schema(task_id=task_id, head=head))
        prompt = REVIEW_SYSTEM_PROMPT.format(
            task_id=task_id, branch=branch, worktree=str(self.worktree),
            base=handover_payload["git_context"]["base_commit"], head=head,
            acceptance=acceptance or "(none provided)",
            handover_json=json.dumps(handover_payload, ensure_ascii=False, indent=1),
            diff=diff[:60000],
        )
        proc = subprocess.run(
            [str(PATHS.agy), "-p", prompt, "--output-format", "json",
             "--json-schema", schema, "--dangerously-skip-permissions"],
            cwd=str(self.worktree), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise AntigravityError(f"agy review rc={proc.returncode}:\n{proc.stderr[-600:]}")
        try:
            envelope = json.loads(proc.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise AntigravityError(f"agy stdout not JSON: {proc.stdout[:400]}") from exc
        if envelope.get("status") != "SUCCESS":
            raise AntigravityError(f"agy review status={envelope.get('status')}: {envelope.get('response','')[:300]}")

        report = envelope.get("structured_output")
        if not isinstance(report, dict):
            raise AntigravityError(f"missing structured_output: {json.dumps(envelope)[:400]}")

        errors = handover.validate_payload(report, "review")
        if errors:
            raise AntigravityError("ReviewReportPayload schema violations: " + "; ".join(errors))

        # post-verification (after schema validation: metadata fields are non-schema)
        dirty_after = self._git("status", "--porcelain")
        report["_worktree_clean_after_review"] = (dirty_before == dirty_after)
        report["_agy_conversation_id"] = envelope.get("conversation_id", "")
        return report


def _agy_review_output_schema(*, task_id: str, head: str) -> dict:
    """Constrained copy of the review schema for agy --json-schema structured output."""
    inner = json.loads(json.dumps(handover.REVIEW_REPORT_SCHEMA))
    inner["required"] = ["schema_version", "task_id", "iteration", "reviewer", "verdict",
                         "verified_head_commit", "findings"]
    props = inner["properties"]
    props["schema_version"] = {"type": "string", "const": "1.1.0"}
    props["task_id"] = {"type": "string", "const": task_id}
    props["verified_head_commit"] = {"type": "string", "const": head}
    props["reviewer"] = {"type": "string", "const": "agent-antigravity"}
    props["iteration"] = {"type": "integer", "minimum": 1, "maximum": 3}
    return inner
