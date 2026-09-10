"""P2-03 decision engines: Final Gate + Review Arbitration (Codex semantics).

Layer split (GPT P2-03 §2):
  Deterministic host = FACTS   (Beads/lease/handover/freshness/git/invariants)
  Codex              = JUDGMENT (did the facts satisfy the human request?)
  Human              = exceptional authority (risk waivers, replan approval)

Both engines: read-only/ephemeral codex calls via CodexCLIInvoker, host-side
schema validation, retry<=2 (second call carries violations, full
regeneration), fail closed (second failure -> DecisionError; Final Gate
escalates the PLAN to ESCALATE_HUMAN, arbitration records the failure).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from orchestrator import decisions
from orchestrator.codex_invoker import CodexCLIInvoker, CodexInvokeError
from orchestrator.gpt_planner import RepoContext  # noqa: F401 (re-use not needed; kept for parity)
from orchestrator.store import Store


class DecisionError(RuntimeError):
    def __init__(self, message: str, *, category: str, evidence: dict | None = None):
        super().__init__(message)
        self.category = category
        self.evidence = evidence or {}


class FinalGateForbidden(DecisionError):
    """Machine evidence missing/invalid — Codex must NOT be called."""


# --------------------------------------------------------------------------
# FinalGateContext: strictly machine-verified facts only
# --------------------------------------------------------------------------

class FinalGateContextBuilder:
    def __init__(self, store: Store, beads, mail) -> None:
        self.store = store
        self.beads = beads
        self.mail = mail

    def build(self, plan_id: str) -> dict:
        plan = self.store.plan(plan_id)
        request = self.store.request(plan.request_id)
        if request is None:
            raise FinalGateForbidden(
                f"original UserRequest not persisted for {plan.request_id}; "
                "semantic acceptance impossible", category="final_gate_forbidden")

        mapping = self.store.mapping(plan_id)
        tasks_ctx = []
        for ptask in plan.tasks:
            task_id = mapping.get(ptask.task_key, "")
            handover = self.store.handover(task_id)
            review = self.store.review(task_id)
            if handover is None:
                raise FinalGateForbidden(f"{ptask.task_key}: no handover", category="final_gate_forbidden")
            from adapters import handover as hm
            if hm.validate_payload(handover, "handover"):
                raise FinalGateForbidden(
                    f"{ptask.task_key}: handover schema-invalid", category="final_gate_forbidden")
            if review is None:
                raise FinalGateForbidden(f"{ptask.task_key}: no review", category="final_gate_forbidden")
            # P2-03 hotfix Fix-3: independent FULL validation before the review
            # may enter the context — schema first, then freshness, then verdict.
            from adapters import handover as _hm
            if _hm.validate_payload(review, "review"):
                raise FinalGateForbidden(
                    f"{ptask.task_key}: review schema-invalid", category="final_gate_forbidden")
            head = handover["git_context"]["head_commit"]
            verified = review.get("verified_head_commit") or ""
            if not (isinstance(verified, str) and len(verified) >= 7):
                raise FinalGateForbidden(
                    f"{ptask.task_key}: verified_head_commit empty/too short",
                    category="final_gate_forbidden")
            if not (review.get("task_id") == task_id
                    and review.get("iteration") == handover.get("iteration")
                    and (verified == head or head.startswith(verified))):
                raise FinalGateForbidden(
                    f"{ptask.task_key}: review not fresh for current handover",
                    category="final_gate_forbidden")
            if review.get("verdict") != "APPROVED":
                raise FinalGateForbidden(
                    f"{ptask.task_key}: effective review is {review.get('verdict')}, not APPROVED",
                    category="final_gate_forbidden")
            tasks_ctx.append({
                "task_key": ptask.task_key,
                "acceptance_criteria": ptask.acceptance_criteria,
                "handover_summary": handover.get("deliverable_summary", ""),
                "known_risks": handover.get("known_risks", []),
                "affected_files": handover.get("affected_files", []),
                "test_evidence": _test_summary(handover),
                "review": {"verdict": "APPROVED", "iteration": review.get("iteration"),
                           "findings_count": len(review.get("findings", []))},
                "verified_head_commit": review.get("verified_head_commit"),
            })

        return {
            "request": request,
            "plan": {"plan_id": plan_id, "summary": plan.summary,
                     "assumptions": plan.assumptions, "risks": plan.risks},
            "tasks": tasks_ctx,
        }


def _test_summary(handover: dict) -> dict:
    ev = handover.get("test_evidence", {})
    return {"command": ev.get("command", ""), "exit_code": ev.get("exit_code"),
            "total": ev.get("total_tests"), "passed": ev.get("passed_tests"),
            "failed": ev.get("failed_tests")}


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------

class _DecisionEngineBase:
    verdicts: tuple[str, ...] = ()

    def __init__(self, *, invoker: CodexCLIInvoker | None = None,
                 prompt_file: str = "", schema: dict | None = None,
                 schema_name: str = "decision", max_calls: int = 2) -> None:
        self.invoker = invoker or CodexCLIInvoker()
        self.prompt = (Path(__file__).resolve().parents[1] / "prompts" / prompt_file) \
            .read_text(encoding="utf-8")
        self.schema = schema
        self.schema_name = schema_name
        self.max_calls = max_calls

    def decide(self, user_content: str) -> dict:
        """One engine call with bounded repair; returns the validated payload."""
        repair: list[str] | None = None
        last_category = "unknown"
        for call in (1, 2):
            if call > self.max_calls:
                break
            content = user_content
            if repair:
                content += ("\n\nYour previous decision was REJECTED by host validation:\n- "
                            + "\n- ".join(repair)
                            + "\n\nRegenerate a COMPLETE corrected decision.")
            try:
                result = self.invoker.invoke(system_prompt=self.prompt, user_content=content,
                                             schema=self.schema, schema_name=self.schema_name)
            except CodexInvokeError as exc:
                last_category = "api_error"
                repair = [f"codex transport failure: {exc}"]
                continue
            payload = result["payload"]
            try:
                self._validate(payload)
            except decisions.DecisionContractError as exc:
                last_category = "contract"
                repair = [str(exc)]
                continue
            grounding = self._grounding_errors(payload)
            if grounding:
                last_category = "grounding"
                repair = grounding
                continue
            return payload
        raise DecisionError(
            f"decision failed after retries ({last_category})",
            category=last_category)

    def _grounding_errors(self, payload: dict) -> list[str]:
        """Engine-level semantic grounding; violations feed the repair retry."""
        return []

    def _validate(self, payload: dict) -> None:
        decisions.validate_decision(payload, self._kind())

    def _kind(self) -> str:
        raise NotImplementedError


class CodexFinalGateEngine(_DecisionEngineBase):
    verdicts = ("APPROVED", "FOLLOWUP_REQUIRED", "ESCALATE_HUMAN")

    def __init__(self, **kw) -> None:
        super().__init__(prompt_file="final-gate-v1.md",
                         schema=decisions.FINAL_GATE_DECISION_WIRE,
                         schema_name="final_gate_decision", **kw)

    def _kind(self) -> str:
        return "final_gate"

    def decide_for_plan(self, ctx: dict) -> dict:
        self._known_task_keys = {t["task_key"] for t in ctx.get("tasks", [])}
        return self.decide("FinalGateContext (machine-verified facts):\n"
                           + json.dumps(ctx, ensure_ascii=False, indent=1))

    def _grounding_errors(self, payload: dict) -> list[str]:
        known = getattr(self, "_known_task_keys", set())
        unknown = sorted({f.get("task_key", "") for f in payload.get("findings", [])}
                         - known - {""})
        if unknown:
            return [f"findings reference unknown task_key(s) {unknown}; "
                    "use an empty task_key or one from the context"]
        return []


class CodexArbitrationEngine(_DecisionEngineBase):
    verdicts = ("REPLAN_REQUIRED", "HUMAN_DECISION_REQUIRED", "ACCEPT_RISK_RECOMMENDATION")

    def __init__(self, **kw) -> None:
        super().__init__(prompt_file="arbitration-v1.md",
                         schema=decisions.ARBITRATION_DECISION_WIRE,
                         schema_name="arbitration_decision", **kw)

    def _kind(self) -> str:
        return "arbitration"

    def decide_for_task(self, ctx: dict) -> dict:
        return self.decide("ArbitrationContext (machine-verified facts):\n"
                           + json.dumps(ctx, ensure_ascii=False, indent=1))
