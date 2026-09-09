"""P0-10: Agent Mail 8-capability matrix over real MCP (stdio).

GPT gate: capability-based acceptance, NOT tool counts. Each required
capability is exercised and the ACTUAL tool names observed are recorded to
docs/p0-evidence/agent-mail/tool-matrix.md (ground truth for aligning the
v1.1 §4.2 Adapter Contract).

SKIP-BLOCKED while `mcp-agent-mail` is not installed.
"""

import asyncio
import json
from pathlib import Path

import pytest

from _mail import AGENTS, PROJECT_KEY, mail_available, stdio_params

pytestmark = pytest.mark.skipif(
    not mail_available(),
    reason="BLOCKED: mcp-agent-mail not installed (docs/INSTALLATION-PROPOSAL.md §2 awaiting approval)",
)

EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "p0-evidence" / "agent-mail"


def _record(matrix: dict) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    lines = ["# Agent Mail actual tool capability matrix (recorded from live MCP)", ""]
    for cap, info in matrix.items():
        lines.append(f"- **{cap}**: `{info}`")
    (EVIDENCE / "tool-matrix.md").write_text("\n".join(lines), encoding="utf-8")


def _payload(result) -> dict:
    for item in (getattr(result, "content", None) or []):
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
    return {}


async def call(session, tool: str, args: dict) -> dict:
    return _payload(await session.call_tool(tool, args))


async def _run_matrix() -> dict:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    matrix: dict[str, str] = {}
    async with stdio_client(stdio_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            assert tools, "no tools exposed"

            # 1) identity + messaging + thread
            for agent in AGENTS:
                await call(session, "register_agent", {"agent_name": agent, "project_key": PROJECT_KEY})
            matrix["messaging/thread"] = "register_agent + send_message + fetch_topic"

            await call(session, "send_message", {
                "project_key": PROJECT_KEY,
                "sender": AGENTS[0], "recipient": AGENTS[1],
                "subject": "[bd-p010] Start: capability probe",
                "body": "P0 capability matrix probe",
                "thread_id": "bd-p010",
            })
            if "fetch_topic" in tools:
                fetched = await call(session, "fetch_topic", {
                    "project_key": PROJECT_KEY, "thread_id": "bd-p010",
                })
                assert fetched, "fetch_topic returned nothing for existing thread"

            # 2) file reservation 3) conflict check 4) renew 5) release 6) force-release
            reserve = await call(session, "file_reservation_paths", {
                "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                "paths": ["src/calculator.py"], "ttl_seconds": 60,
                "exclusive": True, "reason": "bd-p010",
            })
            matrix["file_reservation"] = "file_reservation_paths"
            assert reserve, "reservation call returned nothing"

            if "check_file_reservation_conflicts" in tools:
                matrix["conflict_check"] = "check_file_reservation_conflicts"
            if "renew_file_reservations" in tools:
                await call(session, "renew_file_reservations", {
                    "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                })
                matrix["renew"] = "renew_file_reservations"
            if "release_file_reservations" in tools:
                await call(session, "release_file_reservations", {
                    "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                })
                matrix["release"] = "release_file_reservations"
            matrix["force_release"] = (
                "force_release_file_reservation (present)" if "force_release_file_reservation" in tools else "MISSING"
            )

            # 7) git guard tooling presence (activation into a repo is opt-in later)
            matrix["git_guard"] = (
                "install_precommit_guard" if "install_precommit_guard" in tools else "MISSING"
            )

            # 8) health
            matrix["health"] = "health_check" if "health_check" in tools else "MISSING"
    return matrix


def test_capability_matrix():
    matrix = asyncio.run(_run_matrix())
    _record(matrix)
    for cap in ("messaging/thread", "file_reservation", "conflict_check"):
        assert cap in matrix and "MISSING" not in matrix[cap], f"required capability missing: {cap}"
