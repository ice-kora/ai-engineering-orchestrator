"""P2-03 decision contracts: FinalGateDecision + ArbitrationDecision.

Draft 2020-12 locally; the wire subset (no pattern, additionalProperties:false,
all required) is reused for codex --output-schema. Free text is never a
machine protocol; the host validates every decision payload before acting.
"""

from __future__ import annotations

import jsonschema

# ---- wire subset (codex --output-schema compatible) ----

FINAL_GATE_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "request_coverage", "findings"],
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["APPROVED", "FOLLOWUP_REQUIRED", "ESCALATE_HUMAN"]},
        "summary": {"type": "string"},
        "request_coverage": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requirement", "status", "evidence"],
                "properties": {
                    "requirement": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["SATISFIED", "UNCERTAIN", "MISSING"]},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "description", "recommended_action"],
                "properties": {
                    "severity": {"type": "string",
                                 "enum": ["BLOCKER", "MAJOR", "MINOR"]},
                    "task_key": {"type": "string"},
                    "description": {"type": "string"},
                    "recommended_action": {"type": "string"},
                },
            },
        },
    },
}

ARBITRATION_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "rationale", "binding_directives"],
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["REPLAN_REQUIRED", "HUMAN_DECISION_REQUIRED",
                             "ACCEPT_RISK_RECOMMENDATION"]},
        "rationale": {"type": "string"},
        "binding_directives": {"type": "array", "items": {"type": "string"}},
        "risk_note": {"type": "string"},
    },
}


class DecisionContractError(ValueError):
    pass


def wire_variant(schema: dict) -> dict:
    """Adapt a local schema to the strict-output wire subset: every object's
    `required` must enumerate ALL of its properties (no optional keys on the
    wire). The model fills optional fields with "" / [] when irrelevant; the
    LOCAL schema (with genuinely optional keys) stays the host-side authority.
    """
    import copy
    out = copy.deepcopy(schema)
    if out.get("type") == "object" and "properties" in out:
        out["required"] = sorted(out["properties"].keys())
        for prop in out["properties"].values():
            wire_variant(prop)
    if isinstance(out.get("items"), dict):
        wire_variant(out["items"])
    return out


def validate_decision(payload: dict, kind: str) -> None:
    schema = {"final_gate": FINAL_GATE_DECISION_SCHEMA,
              "arbitration": ARBITRATION_DECISION_SCHEMA}[kind]
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema).iter_errors(payload)]
    if errors:
        raise DecisionContractError("; ".join(errors))


# ---- explicit WIRE schemas (strict-output subset: every key required) ----
# Hand-written literals instead of a recursive converter: wire and local are
# distinct contracts; optional local keys (task_key, risk_note) become
# always-present wire keys the model fills with "" when irrelevant.

FINAL_GATE_DECISION_WIRE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "request_coverage", "findings"],
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["APPROVED", "FOLLOWUP_REQUIRED", "ESCALATE_HUMAN"]},
        "summary": {"type": "string"},
        "request_coverage": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["evidence", "requirement", "status"],
                "properties": {
                    "requirement": {"type": "string"},
                    "status": {"type": "string", "enum": ["SATISFIED", "UNCERTAIN", "MISSING"]},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["description", "recommended_action", "severity", "task_key"],
                "properties": {
                    "severity": {"type": "string", "enum": ["BLOCKER", "MAJOR", "MINOR"]},
                    "task_key": {"type": "string"},
                    "description": {"type": "string"},
                    "recommended_action": {"type": "string"},
                },
            },
        },
    },
}

ARBITRATION_DECISION_WIRE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["binding_directives", "rationale", "risk_note", "verdict"],
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["REPLAN_REQUIRED", "HUMAN_DECISION_REQUIRED",
                             "ACCEPT_RISK_RECOMMENDATION"]},
        "rationale": {"type": "string"},
        "binding_directives": {"type": "array", "items": {"type": "string"}},
        "risk_note": {"type": "string"},
    },
}
