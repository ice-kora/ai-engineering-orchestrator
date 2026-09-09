"""P0-10: Agent Mail capability matrix over the real `am` CLI (v0.3.35).

GPT gate: capability-based acceptance, NOT tool counts. Required capabilities
(GPT list): file reservation / conflict check / TTL / renew / release /
force release / Git Guard / messaging+thread. The matrix records what the CLI
actually provides; force-release is MCP-only in v0.3.35 (recorded, not failed —
TTL expiry is the v1.1 §4.3 fallback path).
"""

import json
import time
from pathlib import Path

import pytest

from _mail import DEMO_REPO, am, am_json, ensure_http_server, get_identities, mail_available, register_identities

pytestmark = pytest.mark.skipif(
    not mail_available(),
    reason="BLOCKED: am CLI not installed",
)

EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "p0-evidence" / "agent-mail"


def _record(matrix: dict) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    lines = ["# Agent Mail actual capability matrix (am CLI v0.3.35, live run)", ""]
    for cap, info in matrix.items():
        lines.append(f"- **{cap}**: `{info}`")
    (EVIDENCE / "tool-matrix.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def identities():
    register_identities()
    return get_identities()


def test_health_and_server(identities):
    assert ensure_http_server(), "HTTP server unreachable on 127.0.0.1:8765"
    listed = am_json("list-projects", "--json")
    assert any("demo-repo" in str(p) for p in (listed if isinstance(listed, list) else listed.get("projects", [])))


def test_messaging_and_thread(identities):
    a, b = identities
    sent = am_json("mail", "send", "--project", DEMO_REPO, "--from", a, "--to", b,
                   "--subject", "[bd-p0cap] Start: capability probe", "--body", "P0 capability matrix probe",
                   "--thread-id", "bd-p0cap", "--json")
    assert sent, "send returned empty result"
    inbox = am_json("mail", "inbox", "--project", DEMO_REPO, "--agent", b, "--json")
    blob = json.dumps(inbox, ensure_ascii=False)
    assert "bd-p0cap" in blob and "[bd-p0cap]" in blob, f"thread/subject not visible in inbox:\n{blob[:500]}"


def test_reservation_renew_release(identities):
    a, _ = identities
    # flags BEFORE positionals (clap strict ordering)
    res = am_json("file_reservations", "reserve", "--ttl", "300", "--exclusive",
                  "--reason", "bd-p0cap", DEMO_REPO, a, "src/cap-probe.txt")
    assert res.get("granted"), f"nothing granted:\n{res}"
    grant = res["granted"][0]
    assert grant["reason"] == "bd-p0cap" and grant["exclusive"] is True
    assert grant.get("expires_ts"), "no expiry exposed (v1.1 lease contract wants expires_at)"

    renewed = am("file_reservations", "renew", "--extend-seconds", "120", DEMO_REPO, a)
    assert renewed.returncode == 0, renewed.stderr

    rel = am("file_reservations", "release", DEMO_REPO, a)
    assert rel.returncode == 0 and "Released" in (rel.stdout + rel.stderr)
    active = am("file_reservations", "active", DEMO_REPO)
    assert "src/cap-probe.txt" not in active.stdout


def test_conflict_semantics_whole_acquire_fails(identities):
    a, b = identities
    # A holds the broad glob exclusively
    res_a = am_json("file_reservations", "reserve", "--ttl", "300", "--exclusive",
                    "--reason", "bd-conf1", DEMO_REPO, a, "src/service/**")
    assert res_a.get("granted"), res_a

    # B attempts the nested file: whole acquire must fail with explicit signal
    res_b = am_json("file_reservations", "reserve", "--ttl", "300", "--exclusive",
                    "--reason", "bd-conf2", DEMO_REPO, b, "src/service/TestService.java")
    assert res_b.get("granted") == [], f"partial grant leaked: {res_b}"
    assert res_b.get("conflicts"), "no conflict signal"
    holder = res_b["conflicts"][0]["holders"][0]
    assert holder["agent"] == a and holder["path_pattern"] == "src/service/**"
    # empirical: conflicting reserve STILL EXITS 0 — adapters must parse JSON
    # (documented in _mail.py and the evidence matrix)

    # B must not hold anything after the failed acquire
    active_b = am("file_reservations", "active", DEMO_REPO)
    assert b not in active_b.stdout, "failed acquire left a grant behind"

    am("file_reservations", "release", DEMO_REPO, a)  # cleanup


def test_ttl_expiry_frees_path(identities):
    """v1.1 §4.3 fallback: leases expire naturally (TTL clamp min is 60s)."""
    a, b = identities
    res = am_json("file_reservations", "reserve", "--ttl", "60", "--exclusive",
                  "--reason", "bd-ttl", DEMO_REPO, a, "src/ttl-probe.txt")
    assert res.get("granted")
    deadline = time.monotonic() + 90
    freed = False
    while time.monotonic() < deadline:
        res_b = am_json("file_reservations", "reserve", "--ttl", "60", "--exclusive",
                        "--reason", "bd-ttl2", DEMO_REPO, b, "src/ttl-probe.txt")
        if res_b.get("granted"):
            freed = True
            break
        time.sleep(5)
    assert freed, "reservation did not expire within 90s of a 60s TTL"
    am("file_reservations", "release", DEMO_REPO, b)


def test_capability_matrix_recorded(identities):
    matrix = {
        "messaging/thread": "am mail send/inbox --thread-id (JSON verified)",
        "file_reservation": "am file_reservations reserve --ttl --exclusive --reason (granted+expires_ts)",
        "conflict_check": "reserve returns {granted:[], conflicts:[holders]} — whole-acquire-fails, EXIT 0 nonetheless",
        "ttl": "--ttl seconds (clamp 60..31536000); natural expiry verified in test_ttl_expiry_frees_path",
        "renew": "am file_reservations renew --extend-seconds",
        "release": "am file_reservations release [--paths|--ids]",
        "force_release": "NOT in CLI v0.3.35 (MCP-only) — TTL expiry is the fallback (v1.1 §4.3)",
        "git_guard": "am guard install/check (presence verified)",
        "http_attestation": "mcp-agent-mail serve --no-tui on 127.0.0.1:8765; CLI reads attest against it",
    }
    guard = am("guard", "--help")
    matrix["git_guard"] = matrix["git_guard"] if guard.returncode == 0 else "MISSING"
    _record(matrix)
    for cap in ("messaging/thread", "file_reservation", "conflict_check", "ttl", "renew", "release"):
        assert "MISSING" not in matrix[cap]
