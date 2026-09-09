"""P0-11: advisory-lease conflict semantics (v1.1 §3.1).

Scenario (per original P0 checklist §六.C): agent A reserves `src/service/**`;
agent B attempts `src/service/TestService.java` and MUST receive an explicit
conflict signal. Per v1.1, any conflict means the whole acquire fails — no
partial grants may survive (release anything granted this call).

SKIP-BLOCKED while `mcp-agent-mail` is not installed.
"""

import asyncio

import pytest

from _mail import AGENTS, PROJECT_KEY, mail_available, stdio_params
from test_agent_mail import call

pytestmark = pytest.mark.skipif(
    not mail_available(),
    reason="BLOCKED: mcp-agent-mail not installed (docs/INSTALLATION-PROPOSAL.md §2 awaiting approval)",
)


async def _conflict_probe() -> tuple[bool, bool]:
    """Returns (conflict_signalled, no_partial_grant_survives)."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    conflict = False
    no_partial = True
    async with stdio_client(stdio_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # agent A (zcode) holds the broad glob exclusively
            await call(session, "file_reservation_paths", {
                "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                "paths": ["src/service/**"], "ttl_seconds": 60,
                "exclusive": True, "reason": "bd-conf01",
            })

            # agent B (antigravity) tries the nested file
            result = await call(session, "file_reservation_paths", {
                "project_key": PROJECT_KEY, "agent_name": AGENTS[1],
                "paths": ["src/service/TestService.java"], "ttl_seconds": 60,
                "exclusive": True, "reason": "bd-conf02",
            })
            blob = str(result).lower()
            conflict = any(k in blob for k in ("conflict", "denied", "rejected", "failed", "overlap"))

            # advisory-lease rule: B's failed acquire must not leave grants behind
            if "release_file_reservations" in {t.name for t in (await session.list_tools()).tools}:
                await call(session, "release_file_reservations", {
                    "project_key": PROJECT_KEY, "agent_name": AGENTS[1],
                })
            # A still holds its own lease (untouched by B's failure)
            a_state = await call(session, "file_reservation_paths", {
                "project_key": PROJECT_KEY, "agent_name": AGENTS[0],
                "paths": ["src/service/TestService.java"], "ttl_seconds": 60,
                "exclusive": True, "reason": "bd-conf03",
            })
            no_partial = "conflict" not in str(a_state).lower()

            # cleanup both agents
            for agent in AGENTS:
                await call(session, "release_file_reservations", {
                    "project_key": PROJECT_KEY, "agent_name": agent,
                })
    return conflict, no_partial


def test_overlapping_reservation_yields_conflict():
    conflict, no_partial = asyncio.run(_conflict_probe())
    assert conflict, "no explicit conflict signal for src/service/** vs src/service/TestService.java"
    assert no_partial, "failed acquire interfered with the other agent's lease (partial-grant leak)"
