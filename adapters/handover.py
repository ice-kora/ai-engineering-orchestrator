"""Machine-verifiable payloads (v1.1 §5) + Draft 2020-12 validation.

Delta vs the v1.1 literals (recorded for GPT, P0 report): real bd 1.2.2 task
ids are ``<repo-prefix>-<rand4>`` (e.g. ``demo-repo-ujn``), so the task_id
pattern here accepts that reality instead of ``^bd-[0-9a-z]{6,10}$``. Everything
else stays aligned with the v1.1 contract shapes.
"""

from __future__ import annotations

from typing import Any

import jsonschema

TASK_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*-[a-z0-9]{3,8}$"
COMMIT_PATTERN = r"^[0-9a-f]{7,40}$"

TASK_HANDOVER_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TaskHandoverPayload",
    "type": "object",
    "required": ["schema_version", "task_id", "iteration", "git_context",
                 "affected_files", "test_evidence", "deliverable_summary", "known_risks"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.1.0"},
        "task_id": {"type": "string", "pattern": TASK_ID_PATTERN},
        "iteration": {"type": "integer", "minimum": 1, "maximum": 3},
        "git_context": {
            "type": "object",
            "required": ["worktree_path", "branch_name", "base_commit", "head_commit"],
            "properties": {
                "worktree_path": {"type": "string"},
                "branch_name": {"type": "string", "pattern": r"^agent/[a-z0-9_-]+/[a-z0-9-]+-[a-z0-9]+$"},
                "base_commit": {"type": "string", "pattern": COMMIT_PATTERN},
                "head_commit": {"type": "string", "pattern": COMMIT_PATTERN},
            },
            "additionalProperties": False,
        },
        "affected_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "test_evidence": {
            "type": "object",
            "required": ["command", "exit_code", "total_tests", "passed_tests",
                         "failed_tests", "output_summary"],
            "properties": {
                "command": {"type": "string"},
                "exit_code": {"type": "integer"},
                "total_tests": {"type": "integer", "minimum": 0},
                "passed_tests": {"type": "integer", "minimum": 0},
                "failed_tests": {"type": "integer", "minimum": 0},
                "output_summary": {"type": "string", "maxLength": 2048},
            },
            "additionalProperties": False,
        },
        "deliverable_summary": {"type": "string", "maxLength": 4096},
        "known_risks": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

REVIEW_REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ReviewReportPayload",
    "type": "object",
    "required": ["schema_version", "task_id", "iteration", "reviewer", "verdict",
                 "verified_head_commit", "findings"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.1.0"},
        "task_id": {"type": "string", "pattern": TASK_ID_PATTERN},
        "iteration": {"type": "integer", "minimum": 1, "maximum": 3},
        "reviewer": {"type": "string"},
        "verdict": {"type": "string", "enum": ["APPROVED", "CHANGES_REQUESTED", "FATAL_REJECT"]},
        "verified_head_commit": {"type": "string", "pattern": COMMIT_PATTERN},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "file_path", "issue_type", "description", "actionable_fix"],
                "properties": {
                    "severity": {"type": "string", "enum": ["BLOCKER", "CRITICAL", "MAJOR", "TRIVIAL"]},
                    "file_path": {"type": "string"},
                    "line_number": {"type": "integer"},
                    "issue_type": {"type": "string",
                                   "enum": ["LOGIC_BUG", "SECURITY_RISK", "TEST_MISSING", "STYLE_DRIFT"]},
                    "description": {"type": "string", "maxLength": 1024},
                    "actionable_fix": {"type": "string", "maxLength": 2048},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def validate_payload(payload: dict, kind: str) -> list[str]:
    """Validate against the Draft 2020-12 schema; returns [] when valid."""
    schema = {"handover": TASK_HANDOVER_SCHEMA, "review": REVIEW_REPORT_SCHEMA}[kind]
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator.iter_errors(payload)]


def build_handover(*, task_id: str, iteration: int, worktree_path: str, branch_name: str,
                   base_commit: str, head_commit: str, affected_files: list[str],
                   test_command: str, test_exit_code: int, total_tests: int, passed_tests: int,
                   failed_tests: int, output_summary: str, deliverable_summary: str,
                   known_risks: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "task_id": task_id,
        "iteration": iteration,
        "git_context": {
            "worktree_path": worktree_path,
            "branch_name": branch_name,
            "base_commit": base_commit,
            "head_commit": head_commit,
        },
        "affected_files": affected_files,
        "test_evidence": {
            "command": test_command,
            "exit_code": test_exit_code,
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "failed_tests": failed_tests,
            "output_summary": output_summary[:2048],
        },
        "deliverable_summary": deliverable_summary[:4096],
        "known_risks": known_risks or [],
    }
