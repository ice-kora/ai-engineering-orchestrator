"""P0-13: Antigravity CLI (agy) headless verification.

Empirical baseline (2026-09-09): agy 1.1.28 @ %LOCALAPPDATA%\\agy\\bin (C-drive
exception approved); credentials shared with the Antigravity desktop IDE via
Windows Credential Manager — headless works WITHOUT a fresh OAuth.

Checks per GPT Round-2 list: version / auth / headless / cwd / worktree /
stdin / stream-json / stdout-stderr / exit code / timeout / json-schema,
plus a targeted repro-or-dismiss of the known worktree repo-detection bug
(google-antigravity/antigravity-cli#68).
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH = PROJECT_ROOT / "sandbox" / "agy-scratch"
DEMO_REPO = PROJECT_ROOT / "sandbox" / "demo-repo"
RUN_TIMEOUT = 240

AGY = str(Path(__file__).resolve().parents[2] / "noop")
import os  # noqa: E402
import shutil  # noqa: E402

AGY = shutil.which("agy") or str(Path(os.environ["LOCALAPPDATA"]) / "agy" / "bin" / "agy.exe")

pytestmark = pytest.mark.skipif(
    not Path(AGY).exists(),
    reason="BLOCKED: agy not installed",
)

EVIDENCE = PROJECT_ROOT / "docs" / "p0-evidence" / "agy"


def agy(*args: str, cwd: Path | None = None, timeout: int = RUN_TIMEOUT,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [AGY, *args], cwd=str(cwd) if cwd else None,
        input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def archive(name: str, argv: list[str], proc: subprocess.CompletedProcess | None, extra: str = "") -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    body = [f"$ {' '.join(map(str, argv))}",
            f"exit={getattr(proc, 'returncode', 'N/A')}",
            "--- stdout ---", ((proc.stdout or "N/A")[:4000] if proc else "N/A"),
            "--- stderr ---", ((proc.stderr or "N/A")[:2000] if proc else "N/A")]
    if extra:
        body += ["--- extra ---", extra]
    (EVIDENCE / f"{name}.txt").write_text("\n".join(body), encoding="utf-8")


def test_version():
    proc = agy("--version", timeout=60)
    archive("version", ["--version"], proc)
    assert proc.returncode == 0 and "1." in proc.stdout


def test_headless_json_envelope_and_streams():
    proc = agy("-p", "Reply with exactly: OK", "--output-format", "json")
    archive("headless-json", ["-p", "...", "--output-format", "json"], proc)
    assert proc.returncode == 0, proc.stderr[-800:]
    env = json.loads(proc.stdout.strip().splitlines()[-1])
    assert env["status"] == "SUCCESS"
    assert env["response"].strip() == "OK"
    assert "usage" in env and "conversation_id" in env
    # streams separated: machine result on stdout, diagnostics on stderr
    assert "conversation_id" not in (proc.stderr or "")


def test_exit_code_reliability():
    proc = agy("--definitely-not-a-flag", timeout=60)
    archive("exit-bad-args", ["--definitely-not-a-flag"], proc)
    assert proc.returncode != 0


def test_cwd_is_honored():
    d = SCRATCH / "cwd"
    d.mkdir(parents=True, exist_ok=True)
    marker = d / "agy-cwd-marker.txt"
    marker.unlink(missing_ok=True)
    proc = agy("-p", "Create a file named agy-cwd-marker.txt in the current working directory "
                "containing exactly: ok, then reply DONE",
               "--output-format", "json", "--dangerously-skip-permissions", cwd=d)
    archive("cwd-honored", ["-p", "...", "cwd=" + str(d)], proc, extra=f"marker={marker.exists()}")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert marker.exists() and marker.read_text(encoding="utf-8").strip() == "ok"


def test_runs_inside_worktree_and_repo_detection():
    """Bug #68 repro-or-dismiss. Empirical 2026-09-09 (agy 1.1.28): rev-parse
    --show-toplevel answered the PARENT repo root, not the worktree — agy
    resolves project root above the worktree. The critical question for the
    Push-Executor design is where WRITES land: file-creation probe below.
    """
    wt = DEMO_REPO / "worktrees" / "agent-zcode"
    if not wt.exists():
        subprocess.run(["git", "-C", str(DEMO_REPO), "worktree", "add", "-b",
                        "agent/zcode/home", str(wt), "main"],
                       capture_output=True, text=True, check=True)
    marker = wt / "agy-wt-marker.txt"
    marker.unlink(missing_ok=True)
    parent_marker = DEMO_REPO / "agy-wt-marker.txt"
    parent_marker.unlink(missing_ok=True)

    proc = agy("-p",
               "Run `git rev-parse --show-toplevel` and reply with ONLY its output.",
               "--output-format", "json", "--dangerously-skip-permissions", cwd=wt)
    archive("inside-worktree-toplevel", ["-p", "git rev-parse", "cwd=" + str(wt)], proc)
    assert proc.returncode == 0, proc.stderr[-800:]
    toplevel = json.loads(proc.stdout.strip().splitlines()[-1])["response"].strip()
    toplevel_is_worktree = str(wt).lower().replace("\\", "/") in toplevel.lower().replace("\\", "/")
    archive("inside-worktree-verdict", ["<verdict>"], None,
            extra=f"toplevel={toplevel!r} matches_worktree={toplevel_is_worktree} "
                  f"(bug #68 {'NOT reproduced' if toplevel_is_worktree else 'CONFIRMED (toplevel=parent)'})")

    proc2 = agy("-p",
                "Create a file named agy-wt-marker.txt in the current working directory "
                "containing exactly: wt, then reply DONE.",
                "--output-format", "json", "--dangerously-skip-permissions", cwd=wt)
    in_wt = marker.exists()
    in_parent = parent_marker.exists()
    archive("inside-worktree-write-probe", ["-p", "create marker", "cwd=" + str(wt)], proc2,
            extra=f"in_worktree={in_wt} in_parent={in_parent}")
    # writes MUST land inside the worktree; leaking to the parent checkout is a
    # hard isolation failure that goes straight to the P0 report for GPT
    assert in_wt and not in_parent, (
        f"write isolation FAILED: in_worktree={in_wt} in_parent={in_parent} "
        f"(toplevel={toplevel!r})"
    )
    marker.unlink(missing_ok=True)
    parent_marker.unlink(missing_ok=True)


def test_stream_json_output():
    proc = agy("-p", "Reply with exactly: OK", "--output-format", "stream-json")
    archive("stream-json", ["-p", "...", "--output-format", "stream-json"], proc)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    # NDJSON events observed; the documented "-p dropped in streaming mode"
    # caveat is confirmed or dismissed by whether a result event exists
    has_result = any(str(p).find("OK") >= 0 for p in parsed)
    archive("stream-json-analysis", ["<analysis>"], None,
            extra=f"ndjson_events={len(parsed)} has_result={has_result}")
    assert len(parsed) >= 1, "no NDJSON events parsed"
    # empirical record: if has_result is False the caveat is confirmed (evidence kept)


def test_stdin_input():
    d = SCRATCH / "stdin"
    d.mkdir(parents=True, exist_ok=True)
    proc = agy("-p", "-", "--output-format", "json", stdin="Reply with exactly: OK", cwd=d)
    archive("stdin-dash", ["-p", "-", "stdin=Reply..."], proc)
    # either works (reply OK) or the '-' prompt convention is unsupported — both are evidence
    if proc.returncode != 0 or "OK" not in proc.stdout:
        proc2 = agy("--output-format", "json", stdin="Reply with exactly: OK", cwd=d)
        archive("stdin-pipe", ["(no -p)", "stdin=Reply..."], proc2)
        assert proc2.returncode == 0 and "OK" in proc2.stdout, (
            "stdin input not accepted in either form (evidence archived)"
        )


def test_timeout_kill_is_safe():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        agy("-p", "Write a detailed 800-word essay about distributed consensus, then reply DONE.",
            timeout=8)
    archive("timeout-kill", ["-p", "long essay (killed at 8s)"], None,
            extra=f"TimeoutExpired after {time.monotonic() - t0:.1f}s")
    assert time.monotonic() - t0 < 60


def test_json_schema_structured_output():
    schema = json.dumps({
        "type": "object",
        "properties": {"sum": {"type": "integer"}},
        "required": ["sum"],
        "additionalProperties": False,
    })
    proc = agy("-p", "What is 17 + 25? Reply with the JSON object {\"sum\": <answer>}.",
               "--output-format", "json", "--json-schema", schema)
    archive("json-schema", ["-p", "17+25", "--json-schema", "..."], proc)
    assert proc.returncode == 0, proc.stderr[-800:]
    env = json.loads(proc.stdout.strip().splitlines()[-1])
    structured = env.get("structured_output") or {}
    assert structured.get("sum") == 42, f"structured_output mismatch: {env}"
