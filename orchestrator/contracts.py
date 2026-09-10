"""P2-01.1 — machine control contracts (JSON Schema Draft 2020-12).

No free-text protocol: every orchestrator boundary speaks these validated
structures. Beads ids follow the runtime reality `<repo-prefix>-<rand4>`
(runtime errata E-01), NOT the v1.1 literal `^bd-…$`.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jsonschema

BEADS_ID = r"^[a-z0-9][a-z0-9-]*-[a-z0-9]{3,8}$"
KEY = r"^[a-z0-9][a-z0-9_-]{1,40}$"

USER_REQUEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "UserRequest",
    "type": "object",
    "required": ["request_id", "target_repo", "title", "description", "constraints", "created_at"],
    "properties": {
        "request_id": {"type": "string", "pattern": r"^req-[0-9a-z-]{3,40}$"},
        "target_repo": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 3, "maxLength": 200},
        "description": {"type": "string", "maxLength": 8000},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "created_at": {"type": "string"},
    },
    "additionalProperties": False,
}

PLANNED_TASK_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PlannedTask",
    "type": "object",
    "required": ["task_key", "title", "description", "target_paths", "acceptance_criteria",
                 "dependencies", "executor_role", "reviewer_role", "risk_level"],
    "properties": {
        "task_key": {"type": "string", "pattern": KEY},
        "title": {"type": "string", "minLength": 3, "maxLength": 200},
        "description": {"type": "string", "maxLength": 8000},
        "target_paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "acceptance_criteria": {"type": "string", "minLength": 3},
        "dependencies": {"type": "array", "items": {"type": "string", "pattern": KEY}},
        "executor_role": {"type": "string", "enum": ["zcode", "antigravity"]},
        "reviewer_role": {"type": "string", "enum": ["zcode", "antigravity"]},
        "risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    },
    "additionalProperties": False,
}

EXECUTION_PLAN_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ExecutionPlan",
    "type": "object",
    "required": ["plan_id", "request_id", "target_repo", "summary", "tasks",
                 "risks", "assumptions", "requires_human_approval"],
    "properties": {
        "plan_id": {"type": "string", "pattern": r"^plan-[0-9a-z-]{3,40}$"},
        "request_id": {"type": "string", "pattern": r"^req-[0-9a-z-]{3,40}$"},
        "target_repo": {"type": "string"},
        "summary": {"type": "string", "maxLength": 2000},
        "tasks": {"type": "array", "items": PLANNED_TASK_SCHEMA, "minItems": 1},
        "risks": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "requires_human_approval": {"type": "boolean"},
    },
    "additionalProperties": False,
}


class ContractError(ValueError):
    pass


def validate(payload: dict, schema: dict) -> None:
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema).iter_errors(payload)]
    if errors:
        raise ContractError("; ".join(errors))


@dataclass
class UserRequest:
    request_id: str
    target_repo: str
    title: str
    description: str
    constraints: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "target_repo": self.target_repo,
                "title": self.title, "description": self.description,
                "constraints": self.constraints, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "UserRequest":
        validate(d, USER_REQUEST_SCHEMA)
        return cls(**d)


@dataclass
class PlannedTask:
    task_key: str
    title: str
    description: str
    target_paths: list[str]
    acceptance_criteria: str
    dependencies: list[str] = field(default_factory=list)
    executor_role: str = "zcode"
    reviewer_role: str = "antigravity"
    risk_level: str = "LOW"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ExecutionPlan:
    plan_id: str
    request_id: str
    target_repo: str
    summary: str
    tasks: list[PlannedTask]
    risks: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    requires_human_approval: bool = True

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id, "request_id": self.request_id,
            "target_repo": self.target_repo, "summary": self.summary,
            "tasks": [t.to_dict() for t in self.tasks],
            "risks": self.risks, "assumptions": self.assumptions,
            "requires_human_approval": self.requires_human_approval,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionPlan":
        validate(d, EXECUTION_PLAN_SCHEMA)
        return cls(plan_id=d["plan_id"], request_id=d["request_id"],
                   target_repo=d["target_repo"], summary=d["summary"],
                   tasks=[PlannedTask(**t) for t in d["tasks"]],
                   risks=d["risks"], assumptions=d["assumptions"],
                   requires_human_approval=d["requires_human_approval"])

    def validate_dag(self) -> None:
        keys = {t.task_key for t in self.tasks}
        if len(keys) != len(self.tasks):
            raise ContractError("duplicate task_key in plan")
        edges: dict[str, list[str]] = {k: [] for k in keys}
        for t in self.tasks:
            unknown = set(t.dependencies) - keys
            if unknown:
                raise ContractError(f"{t.task_key} depends on unknown keys: {unknown}")
            for dep in t.dependencies:
                edges[dep].append(t.task_key)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {k: WHITE for k in keys}

        def dfs(node: str) -> None:
            color[node] = GRAY
            for nxt in edges[node]:
                if color[nxt] == GRAY:
                    raise ContractError(f"dependency cycle at {nxt}")
                if color[nxt] == WHITE:
                    dfs(nxt)
            color[node] = BLACK

        for k in keys:
            if color[k] == WHITE:
                dfs(k)


def new_ids(title_slug: str) -> tuple[str, str]:
    """(request_id, plan_id) with a short readable slug."""
    suffix = uuid.uuid4().hex[:6]
    return f"req-{title_slug}-{suffix}", f"plan-{title_slug}-{suffix}"


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


def load_json(path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
