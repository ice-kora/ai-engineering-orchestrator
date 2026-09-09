"""Pre-P0 Gate #4: verify jsonschema supports Draft 2020-12 using v1.1 §5.1 envelope.

Positive and negative cases prove the validator base required by
docs/architecture_spec_v1.1.md §10 checklist item 4.
"""

import json

import jsonschema
import pytest

# Verbatim from docs/architecture_spec_v1.1.md §5.1 (AgentMessageEnvelope)
ENVELOPE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AgentMessageEnvelope",
    "type": "object",
    "required": [
        "schema_version", "message_id", "operation_id", "idempotency_key",
        "task_id", "sender", "recipient", "message_type", "timestamp", "payload",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": "1.1.0"},
        "message_id": {"type": "string", "pattern": r"^msg-[0-9a-f]{12}$"},
        "operation_id": {
            "type": "string",
            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        },
        "idempotency_key": {"type": "string", "minLength": 8},
        "task_id": {"type": "string", "pattern": r"^bd-[0-9a-z]{6,10}$"},
        "sender": {"type": "string", "enum": ["gpt_orchestrator", "agent_zcode", "agent_antigravity", "human"]},
        "recipient": {"type": "string", "enum": ["gpt_orchestrator", "agent_zcode", "agent_antigravity", "human"]},
        "message_type": {
            "type": "string",
            "enum": ["HANDOVER_FOR_REVIEW", "REVIEW_VERDICT", "ARBITRATION_DECISION", "HEARTBEAT_STATUS"],
        },
        "timestamp": {"type": "integer", "description": "Epoch ms"},
        "payload": {"type": "object"},
    },
    "additionalProperties": False,
}


def valid_envelope() -> dict:
    return {
        "schema_version": "1.1.0",
        "message_id": "msg-0a1b2c3d4e5f",
        # UUIDv7: unix_ms + version 7 + variant [89ab]
        "operation_id": "0192f3e4-a5b6-7c8d-9e0f-a1b2c3d4e5f6",
        "idempotency_key": "agent_zcode:claim:bd-a3f8e9:1",
        "task_id": "bd-a3f8e9",
        "sender": "agent_zcode",
        "recipient": "gpt_orchestrator",
        "message_type": "HANDOVER_FOR_REVIEW",
        "timestamp": 1757390000000,
        "payload": {"summary": "demo"},
    }


def test_draft_2020_12_validator_is_used():
    validator_cls = jsonschema.validators.validator_for(ENVELOPE_SCHEMA)
    assert validator_cls.__name__ == "Draft202012Validator"


def test_valid_envelope_passes():
    jsonschema.Draft202012Validator(ENVELOPE_SCHEMA).validate(valid_envelope())


@pytest.mark.parametrize(
    "mutation, expected_keyword, expected_field",
    [
        # bad task_id pattern
        ({"task_id": "bd-Short"}, "pattern", "task_id"),
        # bad message_id pattern
        ({"message_id": "msg-XYZ"}, "pattern", "message_id"),
        # non-uuidv7 operation_id (version nibble is 4, not 7)
        ({"operation_id": "0192f3e4-a5b6-4c8d-9e0f-a1b2c3d4e5f6"}, "pattern", "operation_id"),
        # unknown enum value
        ({"message_type": "SHOUT"}, "enum", "message_type"),
        # wrong const
        ({"schema_version": "2.0.0"}, "const", "schema_version"),
        # additional property rejected (2020-12 strictness honored)
        ({"extra_field": 1}, "additionalProperties", None),
    ],
)
def test_invalid_envelopes_fail(mutation: dict, expected_keyword: str, expected_field):
    import copy

    broken = valid_envelope()
    broken.update(mutation)
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        jsonschema.Draft202012Validator(ENVELOPE_SCHEMA).validate(broken)
    assert excinfo.value.validator == expected_keyword
    if expected_field is not None:
        assert expected_field in [str(p) for p in excinfo.value.absolute_path]
