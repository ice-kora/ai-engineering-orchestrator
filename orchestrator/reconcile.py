"""P2-01.6-9 — state reconciliation + one-step advancement.

Every decision re-reads live facts (Beads status/labels/dependencies, Agent
Mail reservations, git branch/head, stored handover + review payloads). No
in-memory assumptions survive between calls (F4 guarantee: subprocess reads
only). One reconcile executes AT MOST ONE auto action, then re-reads and
reports — no loops, no daemon.

Auto-executable actions (allow-list):
  * READY_FOR_REVIEW -> start Antigravity review (P1 flow, errata-compliant)
  * READY_FOR_PULL   -> mail "Task Available" + emit human ACTION_REQUIRED
Everything else (pull, implement, fix, close, merge) stays with humans/CLI.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from adapters import handover as handover_mod
from adapters.agent_mail import AgentMailAdapter
from adapters.beads import BeadsAdapter
from orchestrator import contracts
from orchestrator.store import Store


class State(str, Enum):
    WAIT_DEPENDENCY = "WAIT_DEPENDENCY"
    READY_FOR_PULL = "READY_FOR_PULL"
    IMPLEMENTING = "IMPLEMENTING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    FIX_REQUIRED = "FIX_REQUIRED"
    READY_TO_CLOSE = "READY_TO_CLOSE"
    BLOCKED = "BLOCKED"
    INCONSISTENT_CLOSED = "INCONSISTENT_CLOSED"
    DONE = "DONE"
    ESCALATE = "ESCALATE"


class NextAction(str, Enum):
    WAIT = "WAIT"
    NOTIFY_PULL = "NOTIFY_PULL"                # auto: mail + human ACTION_REQUIRED
    ACTION_REQUIRED_PULL = "ACTION_REQUIRED_PULL"  # human: ZCode /pull-task <id>
    START_REVIEW = "START_REVIEW"              # auto (this step)
    START_ARBITRATION = "START_ARBITRATION"    # auto (P2-03: iter>=3 breaker)
    ACTION_REQUIRED_IMPLEMENT = "ACTION_REQUIRED_IMPLEMENT"
    ACTION_REQUIRED_FIX = "ACTION_REQUIRED_FIX"
    ACTION_REQUIRED_CLOSE = "ACTION_REQUIRED_CLOSE"  # human: merge + close
    NONE = "NONE"


@dataclass
class TaskRuntimeState:
    task_key: str
    beads_task_id: str
    state: str
    next_action: str
    beads_status: str = ""
    labels: list[str] = field(default_factory=list)
    deps_remaining: list[str] = field(default_factory=list)
    handover_valid: bool | None = None
    handover_errors: list[str] = field(default_factory=list)
    review_verdict: str | None = None        # EFFECTIVE verdict (fresh only)
    review_iteration: int | None = None
    review_current: bool | None = None       # None: no review pair to compare
    review_stale: bool = False              # a review exists but is not current
    stale_review_verdict: str | None = None
    failed_invariants: list[str] = field(default_factory=list)
    branch: str = ""
    branch_head: str = ""
    merged_to_main: bool = False
    reservation_held: bool = False
    note: str = ""
    action_executed: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Reconciler:
    def __init__(self, store: Store, beads: BeadsAdapter, mail: AgentMailAdapter,
                 final_gate_engine=None, arbitration_engine=None) -> None:
        self.store = store
        self.beads = beads
        self.mail = mail
        self.repo = Path(beads.repo)
        # P2-03 semantic decision engines (deterministic facts stay in code);
        # injectable for tests, defaults lazily to the Codex engines.
        self.final_gate_engine = final_gate_engine
        self.arbitration_engine = arbitration_engine

    # ---- live fact readers (fresh subprocess reads every call; F4) ----

    def _bd_show(self, task_id: str) -> dict:
        import json
        proc = self.beads._run("show", task_id, "--json")
        items = json.loads(proc.stdout or "[]")
        return items[0] if items else {}

    def _labels(self, task_id: str) -> list[str]:
        proc = self.beads._run("label", "list", task_id, check=False)
        return [ln.strip().lstrip("- ").strip() for ln in proc.stdout.splitlines()
                if ln.strip().startswith("- ")]

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    def _branch_facts(self, task_id: str) -> tuple[str, str, bool]:
        branch = f"agent/zcode/{task_id}"
        head = self._git("rev-parse", "--verify", branch)
        if head.returncode != 0:
            return "", "", False
        merged = self._git("merge-base", "--is-ancestor", branch, "main")
        return branch, head.stdout.strip()[:12], merged.returncode == 0

    # ---- review freshness contract (P2-01 hotfix Fix-2) ----

    @staticmethod
    def _head_matches(verified: str, head: str) -> bool:
        """verified may be a short (>=7 char) prefix of the full head."""
        return bool(verified) and bool(head) and (verified == head or head.startswith(verified))

    def _review_is_current(self, handover: dict, review: dict) -> bool:
        return (
            review.get("task_id") == handover.get("task_id")
            and review.get("iteration") == handover.get("iteration")
            and self._head_matches(review.get("verified_head_commit", ""),
                                   handover.get("git_context", {}).get("head_commit", ""))
        )

    # ---- completion invariant (P2-01 hotfix Fix-3) ----

    def _completion_invariant(self, task_id: str, handover: dict | None,
                              handover_valid: bool, review: dict | None,
                              review_current: bool) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if handover is None:
            failures.append("no handover on record")
        elif not handover_valid:
            failures.append("handover schema-invalid")
        if review is None:
            failures.append("no review on record")
        else:
            if not review_current:
                failures.append("review not current (task/iteration/head mismatch)")
            elif review.get("verdict") != "APPROVED":
                failures.append(f"review verdict={review.get('verdict')} (need APPROVED)")
        if handover is not None and handover_valid:
            head = handover.get("git_context", {}).get("head_commit", "")
            merged = self._git("merge-base", "--is-ancestor", head, "main")
            if merged.returncode != 0:
                failures.append("verified head not merged into main")
        if any(r["reason"] == task_id for r in self.mail.active_reservations()):
            failures.append("task reservation still held")
        return (not failures), failures

    # ---- per-task state computation ----

    def task_state(self, plan: contracts.ExecutionPlan, ptask) -> TaskRuntimeState:
        mapping = self.store.mapping(plan.plan_id)
        task_id = mapping.get(ptask.task_key, "")
        if not task_id:
            return TaskRuntimeState(
                task_key=ptask.task_key, beads_task_id="",
                state=State.BLOCKED.value, next_action=NextAction.NONE.value,
                note="not materialized (apply first)")

        show = self._bd_show(task_id)
        status = show.get("status", "")
        labels = self._labels(task_id)

        deps_remaining: list[str] = []
        for dep_key in ptask.dependencies:
            dep_id = mapping.get(dep_key, "")
            dep_status = self._bd_show(dep_id).get("status", "") if dep_id else ""
            if dep_status != "closed":
                deps_remaining.append(dep_key)

        handover = self.store.handover(task_id)
        handover_valid: bool | None = None
        errors: list[str] = []
        if handover is not None:
            errors = handover_mod.validate_payload(handover, "handover")
            handover_valid = not errors
        review = self.store.review(task_id)
        review_current: bool | None = None
        review_stale = False
        review_verdict = None
        review_iter = review.get("iteration") if review else None
        if review is not None and handover is not None:
            review_current = self._review_is_current(handover, review)
            if review_current:
                review_verdict = review.get("verdict")
            else:
                review_stale = True
        elif review is not None:
            review_stale = True  # review without a comparable handover cannot count
        branch, head, merged = self._branch_facts(task_id)
        reservation = any(r["reason"] == task_id for r in self.mail.active_reservations())

        failures: list[str] = []
        if status == "closed":
            ok, failures = self._completion_invariant(
                task_id, handover, bool(handover_valid), review, bool(review_current))
            if ok:
                state, action = State.DONE, NextAction.NONE
            else:
                # closed in Beads but completion facts do not hold — never DONE
                state, action = State.INCONSISTENT_CLOSED, NextAction.NONE
        elif status == "open":
            if deps_remaining:
                state, action = State.WAIT_DEPENDENCY, NextAction.WAIT
            else:
                state, action = State.READY_FOR_PULL, NextAction.NOTIFY_PULL
        else:  # in_progress / blocked
            if handover is not None and not handover_valid:
                state, action = State.BLOCKED, NextAction.NONE
            elif review_verdict == "CHANGES_REQUESTED":
                if (review_iter or 1) >= 3:
                    state = State.ESCALATE
                    action = (NextAction.NONE if self.store.arbitration(task_id)
                              else NextAction.START_ARBITRATION)
                else:
                    state = State.FIX_REQUIRED
                    action = NextAction.ACTION_REQUIRED_FIX
            elif review_verdict == "APPROVED":
                state = State.READY_TO_CLOSE
                action = NextAction.ACTION_REQUIRED_CLOSE
            elif handover_valid:
                # fresh-handover path: stale reviews (incl. old APPROVED) never
                # close a newer head — review reopens instead
                state, action = State.READY_FOR_REVIEW, NextAction.START_REVIEW
            else:
                state, action = State.IMPLEMENTING, NextAction.ACTION_REQUIRED_IMPLEMENT

        note = ""
        if handover is not None and not handover_valid:
            note = "schema-invalid handover: review forbidden"
        elif review_stale:
            note = (f"stale review ignored (task/iteration/head mismatch; "
                    f"old verdict={review.get('verdict')})")
        elif state == State.INCONSISTENT_CLOSED:
            note = "closed but completion invariants failed: " + "; ".join(failures)

        return TaskRuntimeState(
            task_key=ptask.task_key, beads_task_id=task_id,
            state=state.value, next_action=action.value,
            beads_status=status, labels=labels, deps_remaining=deps_remaining,
            handover_valid=handover_valid, handover_errors=errors,
            review_verdict=review_verdict, review_iteration=review_iter,
            review_current=review_current, review_stale=review_stale,
            stale_review_verdict=review.get("verdict") if review_stale else None,
            failed_invariants=failures if state == State.INCONSISTENT_CLOSED else [],
            branch=branch, branch_head=head, merged_to_main=merged,
            reservation_held=reservation,
            note=note)

    # ---- one-step plan reconcile ----

    def reconcile_plan(self, plan_id: str, execute: bool = True) -> dict:
        plan = self.store.plan(plan_id)
        states = [self.task_state(plan, t) for t in plan.tasks]

        executed = ""
        if execute:
            # exactly ONE auto action per call; arbitration > review > notify
            for ptask, st in zip(plan.tasks, states):
                if st.next_action == "START_ARBITRATION":
                    executed = self._start_arbitration(plan, ptask, st)
                    break
            if not executed:
                for ptask, st in zip(plan.tasks, states):
                    if st.state == State.READY_FOR_REVIEW.value:
                        executed = self._start_review(plan, ptask, st)
                        break
            if not executed:
                doc = self.store.plan_doc(plan_id) or {}
                notified = set(doc.get("notified", []))
                for ptask, st in zip(plan.tasks, states):
                    if st.state == State.READY_FOR_PULL.value and ptask.task_key not in notified:
                        executed = self._notify_pull(plan_id, ptask, st)
                        break

        states = [self.task_state(plan, t) for t in plan.tasks]  # re-read after action
        all_done = all(s.state == State.DONE.value for s in states)
        status = self.store.plan_status(plan_id)

        # P2-03 SS4: all tasks mechanically DONE != user request satisfied.
        # APPLIED -> READY_FOR_FINAL_GATE is this reconcile's own transition;
        # the NEXT reconcile may execute START_FINAL_GATE (one step at a time).
        if all_done and status == "APPLIED":
            if self.store.request(plan.request_id) is None:
                executed = executed or ("READY_FOR_FINAL_GATE deferred: original "
                                        "UserRequest not persisted")
            else:
                self.store.set_plan_status(plan_id, "READY_FOR_FINAL_GATE")
                executed = executed or ("READY_FOR_FINAL_GATE (all tasks mechanically "
                                        "DONE; final gate pending)")
                status = "READY_FOR_FINAL_GATE"
        elif (all_done and status == "READY_FOR_FINAL_GATE" and execute
              and self.final_gate_engine and not executed):
            executed = self._run_final_gate(plan_id)   # exactly one action
            status = self.store.plan_status(plan_id)

        return {
            "plan_id": plan_id,
            "plan_status": status,
            "action_executed": executed,
            "tasks": [s.to_dict() for s in states],
            "read_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    # ---- auto actions (allow-list of two) ----

    def _notify_pull(self, plan_id: str, ptask, st: TaskRuntimeState) -> str:
        self.mail.send("B", "A", st.beads_task_id,
                       f"[{st.beads_task_id}] Task Available: {ptask.title}",
                       f"Plan {plan_id} task {ptask.task_key} is ready to pull.")
        doc = self.store.plan_doc(plan_id) or {}
        doc.setdefault("notified", []).append(ptask.task_key)
        self.store._write(f"plans/{plan_id}.json", doc)
        return (f"NOTIFIED + ACTION_REQUIRED: ZCode /pull-task {st.beads_task_id} "
                f"(mail thread {st.beads_task_id})")

    # ---- P2-03: final gate + arbitration (host-executed transitions) ----

    def _run_final_gate(self, plan_id: str) -> str:
        from orchestrator.final_gate import (DecisionError, FinalGateContextBuilder,
                                           FinalGateForbidden)
        self.store.set_plan_status(plan_id, "FINAL_GATE_RUNNING")
        try:
            ctx = FinalGateContextBuilder(self.store, self.beads, self.mail).build(plan_id)
            engine = self.final_gate_engine
            engine = engine if not isinstance(engine, type) else engine()
            decision = engine.decide_for_plan(ctx)
        except FinalGateForbidden as exc:
            self.store.set_plan_status(plan_id, "READY_FOR_FINAL_GATE")
            return f"FINAL_GATE_FORBIDDEN: {exc}"
        except DecisionError as exc:
            self.store.save_final_gate(plan_id, {"error": str(exc), "category": exc.category})
            self.store.set_plan_status(plan_id, "ESCALATED")  # fail closed -> human
            return f"FINAL_GATE failed closed ({exc.category}) -> plan ESCALATED"

        # T6 guard: the model must not invent task keys / commits. Any task_key
        # referenced by the decision has to belong to this plan; otherwise the
        # decision is ungrounded and is rejected outright (never acted upon).
        known_keys = {t.task_key for t in self.store.plan(plan_id).tasks}
        referenced = {f.get("task_key") for f in decision.get("findings", [])
                      if f.get("task_key")}
        unknown = referenced - known_keys
        if unknown:
            self.store.save_final_gate(plan_id, {"error": "ungrounded decision",
                                                 "unknown_task_keys": sorted(unknown),
                                                 "decision_verdict": decision["verdict"]})
            self.store.set_plan_status(plan_id, "ESCALATED")
            return ("FINAL_GATE decision REJECTED (invented task keys "
                    f"{sorted(unknown)}) -> ESCALATED")

        self.store.save_final_gate(plan_id, decision)
        if decision["verdict"] == "APPROVED":
            ok, failures = self._plan_done_invariant(plan_id)  # host re-verification
            if not ok:
                self.store.set_plan_status(plan_id, "ESCALATED")
                return ("FINAL_GATE APPROVED but host invariant failed: "
                        + "; ".join(failures) + " -> ESCALATED")
            self.store.set_plan_status(plan_id, "DONE")
            return "FINAL_GATE APPROVED + host invariant OK -> DONE"
        if decision["verdict"] == "FOLLOWUP_REQUIRED":
            self.store.set_plan_status(plan_id, "FINAL_FIX_REQUIRED")
            return ("FINAL_GATE FOLLOWUP_REQUIRED -> FINAL_FIX_REQUIRED | "
                    "ACTION_REQUIRED: REPLAN (no auto task creation)")
        self.store.set_plan_status(plan_id, "ESCALATED")
        return "FINAL_GATE ESCALATE_HUMAN -> ESCALATED"

    def _plan_done_invariant(self, plan_id: str) -> tuple[bool, list[str]]:
        plan = self.store.plan(plan_id)
        failures: list[str] = []
        for ptask in plan.tasks:
            st = self.task_state(plan, ptask)
            if st.state != State.DONE.value:
                failures.append(f"{ptask.task_key}: state={st.state}")
            if st.reservation_held:
                failures.append(f"{ptask.task_key}: reservation still held")
            if st.review_verdict != "APPROVED":
                failures.append(f"{ptask.task_key}: effective review not APPROVED")
            if not st.merged_to_main:
                failures.append(f"{ptask.task_key}: verified head not merged")
        return (not failures), failures

    def _start_arbitration(self, plan: contracts.ExecutionPlan, ptask,
                           st: TaskRuntimeState) -> str:
        from orchestrator.final_gate import DecisionError
        task_id = st.beads_task_id
        handover = self.store.handover(task_id) or {}
        review = self.store.review(task_id) or {}
        iterations = review.get("iteration") or 3
        # P2-03 hotfix Fix-2: arbitration consumes the REAL append-only history.
        # A claimed iteration>=3 without complete, valid 1..N history is
        # FORBIDDEN — Codex is not called and no data is faked.
        history = self.store.review_history(task_id)
        reviews_ctx = []
        for it in range(1, iterations + 1):
            item = history.get(it)
            if item is None:
                self.store.save_arbitration(task_id, {
                    "error": f"review history incomplete: iteration {it} missing",
                    "category": "arbitration_forbidden"})
                return (f"ARBITRATION_FORBIDDEN: review history incomplete "
                        f"(iteration {it} missing); task stays ESCALATED")
            if item.get("verdict") != "CHANGES_REQUESTED":
                self.store.save_arbitration(task_id, {
                    "error": f"history iteration {it} verdict={item.get('verdict')}",
                    "category": "arbitration_forbidden"})
                return (f"ARBITRATION_FORBIDDEN: history iteration {it} is not "
                        f"CHANGES_REQUESTED; task stays ESCALATED")
            reviews_ctx.append({k: v for k, v in item.items()
                                if not str(k).startswith("_")})
        ctx = {
            "task_key": ptask.task_key,
            "acceptance_criteria": ptask.acceptance_criteria,
            "review_iterations": iterations,
            "reviews": reviews_ctx,
            "latest_effective_review": {k: v for k, v in review.items()
                                        if not str(k).startswith("_")},
            "handover_summary": handover.get("deliverable_summary", ""),
            "test_evidence": handover.get("test_evidence", {}),
            "verified_head_commit": review.get("verified_head_commit"),
        }
        try:
            engine = self.arbitration_engine
            engine = engine if not isinstance(engine, type) else engine()
            decision = engine.decide_for_task(ctx)
        except DecisionError as exc:
            self.store.save_arbitration(task_id, {"error": str(exc), "category": exc.category})
            return f"ARBITRATION failed closed ({exc.category}); task stays ESCALATED"
        self.store.save_arbitration(task_id, decision)
        verdict = decision["verdict"]
        if verdict == "ACCEPT_RISK_RECOMMENDATION":
            return ("ARBITRATION ACCEPT_RISK_RECOMMENDATION recorded (recommendation ONLY; "
                    "task NOT closed - human waiver required)")
        if verdict == "REPLAN_REQUIRED":
            return "ARBITRATION REPLAN_REQUIRED | ACTION_REQUIRED: REPLAN (no auto task creation)"
        return "ARBITRATION HUMAN_DECISION_REQUIRED | ACTION_REQUIRED: human decision"

    def _start_review(self, plan: contracts.ExecutionPlan, ptask, st: TaskRuntimeState) -> str:
        from adapters.review_flow import review_iteration  # local import: agy dependency
        payload = self.store.handover(st.beads_task_id)
        report = review_iteration(repo=self.repo, task=ptask.__dict__,
                                   acceptance=ptask.acceptance_criteria,
                                   handover_payload=payload)
        self.store.save_review(st.beads_task_id, report)
        return (f"START_REVIEW executed: verdict={report.get('verdict')} "
                f"iteration={report.get('iteration')} "
                f"worktree_clean={report.get('_worktree_clean_after_review')}")
