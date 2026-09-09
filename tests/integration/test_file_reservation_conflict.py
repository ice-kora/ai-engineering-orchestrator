"""P0-11: advisory-lease conflict semantics (v1.1 §3.1) — original P0 §六.C scenario.

Agent A reserves `src/service/**`; agent B attempts
`src/service/TestService.java` and MUST receive an explicit conflict signal
with the whole acquire failing (no partial grants, B holds nothing, A
unaffected). Also documents the exit-code-0-on-conflict empirical rule.
"""

import pytest

from _mail import DEMO_REPO, am, am_json, get_identities, mail_available, register_identities

pytestmark = pytest.mark.skipif(
    not mail_available(),
    reason="BLOCKED: am CLI not installed",
)


def test_overlap_conflict_signal_and_isolation():
    a, b = register_identities()

    # clean slate for the probe paths
    am("file_reservations", "release", DEMO_REPO, a)
    am("file_reservations", "release", DEMO_REPO, b)

    res_a = am_json("file_reservations", "reserve", "--ttl", "300", "--exclusive",
                    "--reason", "bd-c11", DEMO_REPO, a, "src/service/**")
    assert res_a.get("granted"), res_a

    # B's attempt on the nested file
    proc = am("file_reservations", "reserve", "--ttl", "300", "--exclusive",
              "--reason", "bd-c12", DEMO_REPO, b, "src/service/TestService.java")
    import json as _json
    res_b = _json.loads(proc.stdout)
    assert proc.returncode == 0  # empirical: conflicts do NOT flip the exit code
    assert res_b.get("granted") == [], "advisory-lease violation: partial grant on conflict"
    conflicts = res_b.get("conflicts", [])
    assert conflicts, "no explicit conflict signal"
    assert conflicts[0]["path"] == "src/service/TestService.java"
    assert conflicts[0]["holders"][0]["agent"] == a

    # B holds nothing after the failed acquire
    active = am("file_reservations", "active", DEMO_REPO)
    assert b not in active.stdout

    # A's lease is unaffected (A can re-reserve its own glob)
    res_a2 = am_json("file_reservations", "reserve", "--ttl", "300", "--exclusive",
                     "--reason", "bd-c11", DEMO_REPO, a, "src/service/**")
    assert res_a2.get("granted") or not res_a2.get("conflicts"), res_a2

    # semantic conclusion for the workflow layer: with a non-empty conflicts
    # list the executor is FORBIDDEN from touching the files (v1.1 §3.1) —
    # encoded here as: no grant id for B exists to renew or release.
    b_active = am("file_reservations", "list", DEMO_REPO)
    assert b not in b_active.stdout

    am("file_reservations", "release", DEMO_REPO, a)
