"""P2-04 RunController — the deterministic control loop.

observe -> determine next safe step -> execute ONE allowed action from the
existing verified components -> journal -> re-read -> stop at a human boundary
or terminal state. NOT a daemon; bounded pump only.

Layer separation (NEVER violated here):
  Beads = task status authority | Git = code/evidence authority
  Mail = lease/communication facts | Store = orchestrator-owned state
  Reconciler = runtime fact interpreter | Codex = semantic judgment
The controller OWNS none of these; every step re-reads live state through the
existing Materializer/Reconciler/ApprovalGate/Store APIs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator import journal
from orchestrator.final_gate import CodexArbitrationEngine, CodexFinalGateEngine
from orchestrator.reconcile import Reconciler, State


class RunControllerError(RuntimeError):
    pass


@dataclass
class RunResult:
    plan_id: str
    outcome: str                 # PAUSED | DONE | FAILED | STALLED
    plan_status: str
    steps_executed: int = 0
    last_action: str = ""
    human_action_required: bool = False
    action_required: dict = field(default_factory=dict)
    recovery_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id, "outcome": self.outcome,
            "plan_status": self.plan_status,
            "steps_executed": self.steps_executed,
            "last_action": self.last_action,
            "human_action_required": self.human_action_required,
            "action_required": self.action_required,
            "recovery_notes": self.recovery_notes,
        }


# Human boundary map: state -> structured pause instruction
_HUMAN_BOUNDARIES: dict[str, dict] = {
    "READY_FOR_PULL": {
        "type": "ACTION_REQUIRED_PULL",
        "instruction": "ZCode: run /pull-task (scripts/pull_task.py --task <task_id>)",
    },
    "IMPLEMENTING": {
        "type": "WAIT_EXECUTOR",
        "instruction": "ZCode is implementing; run `continue` again after handover is stored",
    },
    "FIX_REQUIRED": {
        "type": "ACTION_REQUIRED_FIX",
        "instruction": "ZCode: apply reviewer findings, then store a new handover iteration",
    },
    "READY_TO_CLOSE": {
        "type": "ACTION_REQUIRED_CLOSE",
        "instruction": "Human: merge branch to main and close the Beads task (auto-merge is disabled in P2-04)",
    },
    "FINAL_FIX_REQUIRED": {
        "type": "ACTION_REQUIRED_REPLAN",
        "instruction": "Human: final gate reported gaps; replan via `orchestrate plan` when ready",
    },
    "ESCALATED": {
        "type": "ACTION_REQUIRED_HUMAN",
        "instruction": "Human decision required (escalated by gate/arbitration/failure)",
    },
    "INCONSISTENT_CLOSED": {
        "type": "ACTION_REQUIRED_INVESTIGATE",
        "instruction": "Closed task failed completion invariants; investigate and repair",
    },
}

_TERMINAL = {"DONE", "REJECTED"}


class RunController:
    """Bounded pump over the existing one-step Reconciler.

    Allow-listed automatic actions (all pre-verified components):
      PLANNED -> (none: approval is a human boundary)
      APPROVED -> Materializer.apply (idempotent)
      everything else -> Reconciler.reconcile_plan(execute=True) which itself
      performs at most one auto action (notify/review/arbitration/final-gate)
    """

    def __init__(self, store, beads, mail, *, evidence_dir=None,
                 final_gate_engine=None, arbitration_engine=None):
        self.store = store
        self.beads = beads
        self.mail = mail
        self._fg = final_gate_engine
        self._arb = arbitration_engine
        self._evidence_dir = evidence_dir

    def _engines(self):
        return {"final_gate_engine": self._fg or CodexFinalGateEngine(),
                "arbitration_engine": self._arb or CodexArbitrationEngine()}

    # ---- pre-loop recovery (C2/C3) ----

    def _recover(self, plan_id: str) -> list[str]:
        notes: list[str] = []
        # C2: review history exists but latest view missing/stale
        for task_key, task_id in self.store.mapping(plan_id).items():
            repaired = journal.repair_review_latest(self.store, task_id)
            if repaired is not None:
                notes.append(f"C2: repaired latest review view for {task_key} "
                             f"(iteration {repaired.get('iteration')}) from immutable history")
        # C3: final-gate decision persisted but plan status not yet migrated.
        # Replay MUST re-validate the persisted decision (schema + verdict +
        # grounding) — recovery never weakens the normal-path gates.
        status = self.store.plan_status(plan_id)
        decision = self.store.final_gate(plan_id)
        if decision and "verdict" in decision and status == "FINAL_GATE_RUNNING":
            errors = self._validate_persisted_decision(plan_id, decision)
            if errors:
                # INVALID persisted decision: fail closed. Preserve the bad
                # evidence for forensic inspection; never re-call Codex; never
                # transition to DONE or FINAL_FIX_REQUIRED.
                self.store.set_plan_status(plan_id, "ESCALATED")
                notes.append("INVALID_PERSISTED_FINAL_GATE_DECISION: " + "; ".join(errors))
            else:
                notes.append(f"C3: replaying host transition for persisted decision "
                             f"({decision['verdict']}) without re-calling Codex")
                self._apply_final_decision_transition(plan_id, decision)
        return notes

    def _validate_persisted_decision(self, plan_id: str, decision: dict) -> list[str]:
        """Full re-validation of a persisted final-gate decision (P2-04 hotfix).

        Checks (same rigor as the normal path):
        1. decisions.validate_decision(decision, "final_gate") — full contract
        2. verdict in the three-value enum
        3. findings[].task_key grounding: empty or belongs to this plan
        Returns [] when valid; non-empty errors => fail closed.
        """
        from orchestrator import decisions as _dec
        errors: list[str] = []

        # 1. full contract validation (schema + structure completeness)
        try:
            _dec.validate_decision(decision, "final_gate")
        except _dec.DecisionContractError as exc:
            errors.append(f"schema: {exc}")
            return errors  # no point grounding an ill-formed payload

        # 2. verdict enum (belt-and-suspenders: schema already enforces this,
        #    but a tampered/corrupted file might bypass — verify independently)
        verdict = decision.get("verdict")
        if verdict not in ("APPROVED", "FOLLOWUP_REQUIRED", "ESCALATE_HUMAN"):
            errors.append(f"verdict '{verdict}' not in enum")

        # 3. grounding: findings[].task_key must be "" or a real plan task_key
        known_keys = {t.task_key for t in self.store.plan(plan_id).tasks}
        referenced = {f.get("task_key", "") for f in decision.get("findings", [])
                      if f.get("task_key")}
        unknown = referenced - known_keys
        if unknown:
            errors.append(f"grounding: unknown task_key(s) {sorted(unknown)}")

        return errors

    def _apply_final_decision_transition(self, plan_id: str, decision: dict) -> None:
        """Host-side transition replay for a persisted final-gate decision."""
        verdict = decision["verdict"]
        if verdict == "APPROVED":
            recon = Reconciler(self.store, self.beads, self.mail, **self._engines())
            ok, failures = recon._plan_done_invariant(plan_id)
            if ok:
                self.store.set_plan_status(plan_id, "DONE")
            else:
                self.store.set_plan_status(plan_id, "ESCALATED")
        elif verdict == "FOLLOWUP_REQUIRED":
            self.store.set_plan_status(plan_id, "FINAL_FIX_REQUIRED")
        else:
            self.store.set_plan_status(plan_id, "ESCALATED")

    # ---- bounded pump ----

    def continue_run(self, plan_id: str, *, max_steps: int = 10,
                     max_wall_seconds: float = 300.0) -> RunResult:
        t0 = time.monotonic()
        steps = 0
        recovery = self._recover(plan_id)
        last_fingerprint = None
        stall_count = 0
        last_action = ""

        while steps < max_steps and (time.monotonic() - t0) < max_wall_seconds:
            status = self.store.plan_status(plan_id)

            # terminal
            if status in _TERMINAL:
                return RunResult(plan_id, "DONE" if status == "DONE" else "FAILED",
                                 status, steps, last_action,
                                 recovery_notes=recovery)

            # PLANNED: approval is a hard human boundary
            if status == "PLANNED":
                return self._pause(plan_id, steps, last_action, recovery,
                                   {"type": "HUMAN_APPROVAL_REQUIRED",
                                    "task_id": "", "task_key": "",
                                    "instruction": f"orchestrate approve {plan_id}"})

            # APPROVED -> idempotent apply (allow-list)
            # APPLIED but incomplete mapping -> re-apply (C1 crash recovery:
            # a partial apply sets APPLIED; Materializer is idempotent and
            # heals toward the live Beads truth)
            mapping = self.store.mapping(plan_id)
            plan_tasks = {t.task_key for t in self.store.plan(plan_id).tasks}
            needs_apply = (status == "APPROVED"
                           or (status == "APPLIED" and set(mapping) != plan_tasks))
            if needs_apply:
                from orchestrator.materialize import Materializer
                report = Materializer(self.store, self.beads,
                                      __import__("orchestrator.approval",
                                                 fromlist=["ApprovalGate"]).ApprovalGate(self.store)
                                      ).apply(self.store.plan(plan_id))
                action = (f"APPLY executed: created={report.created} "
                          f"reused={report.reused} count={report.beads_task_count}")
                self._journal(plan_id, status, action, "ok")
                steps += 1
                last_action = action
                # fingerprint for stall detection
                fp = ("APPLY", tuple(sorted(report.created)))
                if fp == last_fingerprint:
                    stall_count += 1
                else:
                    stall_count = 0
                last_fingerprint = fp
                continue

            # everything else: delegate ONE auto action to Reconciler
            recon = Reconciler(self.store, self.beads, self.mail, **self._engines())
            r = recon.reconcile_plan(plan_id, execute=True)
            action = r["action_executed"] or ""
            task_states = {t["task_key"]: t["state"] for t in r["tasks"]}
            self._journal(plan_id, r["plan_status"], action, r["plan_status"], task_states)
            steps += 1
            last_action = action

            # stall detection: same fingerprint twice with no action => STOP
            fp = (r["plan_status"], action)
            if fp == last_fingerprint and not action:
                stall_count += 1
                if stall_count >= 2:
                    return RunResult(plan_id, "STALLED", r["plan_status"], steps,
                                     last_action, recovery_notes=recovery + ["no progress for 2 steps"])
            elif fp == last_fingerprint:
                stall_count += 1
                if stall_count >= 2:
                    return RunResult(plan_id, "STALLED", r["plan_status"], steps,
                                     last_action, recovery_notes=recovery + ["same action repeated"])
            else:
                stall_count = 0
            last_fingerprint = fp

            # human boundary?
            boundary_states = {s for s in task_states.values() if s in _HUMAN_BOUNDARIES}
            if boundary_states and not action.startswith("NOTIFIED"):
                # notify is auto; the PULL itself is still human — pause on it
                for key, st in task_states.items():
                    if st == "READY_FOR_PULL":
                        tid = next(t["beads_task_id"] for t in r["tasks"]
                                   if t["task_key"] == key)
                        return self._pause(plan_id, steps, action, recovery,
                                           {**_HUMAN_BOUNDARIES["READY_FOR_PULL"],
                                            "task_id": tid, "task_key": key})
                # other boundaries: pick the first task in that state
                for t in r["tasks"]:
                    if t["state"] in _HUMAN_BOUNDARIES and t["state"] != "READY_FOR_PULL":
                        return self._pause(plan_id, steps, action, recovery,
                                           {**_HUMAN_BOUNDARIES[t["state"]],
                                            "task_id": t["beads_task_id"],
                                            "task_key": t["task_key"]})

            # plan-level terminal after reconcile (final gate may have run)
            final_status = self.store.plan_status(plan_id)
            if final_status == "DONE":
                return RunResult(plan_id, "DONE", final_status, steps, last_action,
                                 recovery_notes=recovery)
            if final_status in ("FINAL_FIX_REQUIRED", "ESCALATED"):
                return self._pause(plan_id, steps, last_action, recovery,
                                   _HUMAN_BOUNDARIES[final_status])

        # bounded exit
        status = self.store.plan_status(plan_id)
        return RunResult(plan_id, "STALLED" if steps >= max_steps else "PAUSED",
                         status, steps, last_action, recovery_notes=recovery +
                         [f"bounded exit: steps={steps} wall={time.monotonic()-t0:.0f}s"])

    def _pause(self, plan_id, steps, last_action, recovery, action_required) -> RunResult:
        return RunResult(plan_id, "PAUSED", self.store.plan_status(plan_id),
                         steps, last_action, True, action_required, recovery)

    def _journal(self, plan_id, observed_status, action, outcome, task_states=None):
        journal.append_run_event(self.store, plan_id, {
            "observed_plan_status": observed_status,
            "observed_task_states": task_states or {},
            "intended_action": action[:200],
            "action_result": outcome,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "outcome": outcome,
        })

    # ---- read-only snapshot (P2-04 §14) ----

    def snapshot(self, plan_id: str) -> dict:
        """PlanSnapshot: computed from live facts; never a writable state."""
        recon = Reconciler(self.store, self.beads, self.mail)
        r = recon.reconcile_plan(plan_id, execute=False)
        gate = self.store.final_gate(plan_id)
        return {
            "plan_id": plan_id,
            "plan_status": r["plan_status"],
            "computed_at": r["read_at"],
            "tasks": [{"task_key": t["task_key"],
                       "beads_task_id": t["beads_task_id"],
                       "runtime_state": t["state"],
                       "next_action": t["next_action"],
                       "review_iteration": t.get("review_iteration"),
                       "review_verdict": t.get("review_verdict"),
                       "review_stale": t.get("review_stale", False),
                       "lease_held": t.get("reservation_held", False),
                       "merged_to_main": t.get("merged_to_main", False),
                       "human_boundary": t["state"] in _HUMAN_BOUNDARIES}
                      for t in r["tasks"]],
            "final_gate": ({k: v for k, v in gate.items()
                            if k in ("verdict", "summary")} if gate else None),
            "journal_length": len(journal.run_events(self.store, plan_id)),
        }
