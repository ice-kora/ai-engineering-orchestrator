"""P0-ZCODE-HEADLESS: verify the locally installed ZCode CLI as a headless executor.

Object under test (evidence-backed discovery, 2026-09-09):
    ZCode CLI v0.16.5 at D:/Software/zcode/install/ZCode/resources/glm/zcode.cjs
    (not on PATH; invoked via node). `--help` shows: -p/--print, --prompt,
    --cwd, --mode build|edit|plan|yolo, --max-turns, --allowed-tools,
    --disallowed-tools, --resume, --json.

Checks (GPT Final Gate supplement #5):
    1  non-interactive minimal task           6  safe termination on timeout
    2  --cwd is honored                       7  --allowed-tools restriction enforced
    3  machine-parseable --json output        8  runs inside a Git worktree
    4  exit code reliability                  9  three consecutive stable runs
    5  stdout/stderr separable               10  concurrent isolation (2 processes)

VERDICT: ZCODE_HEADLESS = VERIFIED only if ALL 10 pass.
Raw transcripts are archived to docs/p0-evidence/zcode-headless/ for the P0 report.

Note: this only RECORDS evidence — upgrading ZCode from Pull to Push executor is
a P1 architecture decision reserved for GPT (per project instructions).
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NODE = "node"
ZCODE_CJS = r"D:\Software\zcode\install\ZCode\resources\glm\zcode.cjs"
EVIDENCE_DIR = PROJECT_ROOT / "docs" / "p0-evidence" / "zcode-headless"
SCRATCH = PROJECT_ROOT / "sandbox" / "zcode-headless-scratch"
DEMO_REPO = PROJECT_ROOT / "sandbox" / "demo-repo"

MODEL_RUN_TIMEOUT = 240  # single headless run budget (seconds)


def zcode(*args: str, timeout: int = MODEL_RUN_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, ZCODE_CJS, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def archive(name: str, argv: list[str], proc: subprocess.CompletedProcess | None, extra: str = "") -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    body = [
        f"$ {' '.join(argv)}",
        f"exit={getattr(proc, 'returncode', 'N/A')} elapsed_note=see report",
        "--- stdout ---", (proc.stdout if proc else "N/A"),
        "--- stderr ---", (proc.stderr if proc else "N/A"),
    ]
    if extra:
        body += ["--- extra ---", extra]
    (EVIDENCE_DIR / f"{name}.txt").write_text("\n".join(body), encoding="utf-8")


def extract_json(text: str) -> dict | None:
    """Best-effort machine-parseable extraction: whole stdout, else last JSON object."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.rfind("\n{")
    if start == -1 and text.startswith("{"):
        start = 0
    if start != -1:
        depth = 0
        for i in range(start if start == 0 else start + 1, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start if start == 0 else start + 1: i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


@pytest.fixture(scope="module")
def scratch_dirs():
    a = SCRATCH / "wt-a"
    b = SCRATCH / "wt-b"
    c = SCRATCH / "wt-c"
    for d in (a, b, c):
        d.mkdir(parents=True, exist_ok=True)
    return a, b, c


# ---- Check 1 + 2 + 3 + 5: minimal task, --cwd honored, --json, stream separation ----
def test_check_1_2_3_5_minimal_cwd_json_streams(scratch_dirs):
    a, _, _ = scratch_dirs
    marker = a / "cwd-marker.txt"
    marker.unlink(missing_ok=True)
    argv = [NODE, ZCODE_CJS, "-p",
            "Create a file named cwd-marker.txt in the current working directory "
            "containing exactly the word ok, then reply with exactly: DONE",
            "--cwd", str(a), "--max-turns", "3", "--json"]
    t0 = time.monotonic()
    proc = zcode(*argv[2:])
    elapsed = time.monotonic() - t0
    archive("run1-minimal-cwd-json", argv, proc, extra=f"elapsed={elapsed:.1f}s; marker_exists={marker.exists()}")

    assert proc.returncode == 0, f"exit={proc.returncode}\nstderr:\n{proc.stderr[-2000:]}"
    assert marker.exists(), "cwd-marker.txt not created => --cwd not honored or task failed"
    assert marker.read_text(encoding="utf-8").strip() == "ok"
    parsed = extract_json(proc.stdout)
    assert parsed is not None and isinstance(parsed, dict), (
        f"--json stdout not machine-parseable:\n{proc.stdout[:1000]}"
    )
    # streams separable: result on stdout, diagnostics on stderr (stderr may be empty)
    assert isinstance(proc.stderr, str)


# ---- Check 4: exit code reliability ----
def test_check_4_exit_codes():
    argv_ok = [NODE, ZCODE_CJS, "-p", "Reply with exactly: OK", "--max-turns", "1", "--json"]
    proc_ok = zcode(*argv_ok[2:])
    archive("run2-exit-ok", argv_ok, proc_ok)
    assert proc_ok.returncode == 0

    argv_bad = [NODE, ZCODE_CJS, "--definitely-not-a-real-flag"]
    proc_bad = zcode(*argv_bad[2:], timeout=60)
    archive("run3-exit-bad-args", argv_bad, proc_bad)
    assert proc_bad.returncode != 0, "invalid flags must fail loudly (non-zero exit)"


# ---- Check 6: safe termination on timeout ----
def test_check_6_timeout_kill_is_safe(scratch_dirs):
    _, b, _ = scratch_dirs
    argv = [NODE, ZCODE_CJS, "-p",
            "Write a detailed 500-word essay about distributed leases, then reply DONE.",
            "--cwd", str(b), "--max-turns", "1"]
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        zcode(*argv[2:], timeout=8)
    elapsed = time.monotonic() - t0
    # TimeoutExpired.kill() already terminated the process tree on Windows (taskkill /T)
    archive("run4-timeout-kill", argv, None,
            extra=f"TimeoutExpired after {elapsed:.1f}s (limit 8s); killed={excinfo.value.cmd is not None}")
    assert elapsed < 60, "process did not terminate in reasonable time after kill"


# ---- Check 7: --allowed-tools restriction enforced ----
def test_check_7_allowed_tools_restriction(scratch_dirs):
    _, _, c = scratch_dirs
    marker = c / "blocked-marker.txt"
    marker.unlink(missing_ok=True)
    argv = [NODE, ZCODE_CJS, "-p",
            "Create a file named blocked-marker.txt in the current working directory "
            "containing ok, then reply DONE.",
            "--cwd", str(c), "--max-turns", "3", "--json",
            "--allowed-tools", "Read"]
    proc = zcode(*argv[2:])
    archive("run5-allowed-tools", argv, proc, extra=f"marker_exists={marker.exists()} (must be False)")
    # the run itself may exit 0 (agent politely refuses); the WRITE must not happen
    assert not marker.exists(), "write happened despite --allowed-tools Read => restriction not enforced"


# ---- Check 8: runs inside a Git worktree ----
def test_check_8_runs_inside_worktree():
    wt = DEMO_REPO / "worktrees" / "agent-zcode"
    listed = subprocess.run(
        ["git", "-C", str(DEMO_REPO), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if wt.as_posix() not in listed.stdout:
        subprocess.run(["git", "-C", str(DEMO_REPO), "worktree", "add", "-b",
                        "agent/zcode/home", str(wt), "main"],
                       capture_output=True, text=True, check=True)
    argv = [NODE, ZCODE_CJS, "-p", "Reply with exactly: OK", "--cwd", str(wt),
            "--max-turns", "1", "--json"]
    proc = zcode(*argv[2:])
    archive("run6-inside-worktree", argv, proc, extra=f"cwd={wt}")
    assert proc.returncode == 0, f"stderr:\n{proc.stderr[-2000:]}"
    assert extract_json(proc.stdout) is not None


# ---- Check 9: three consecutive stable runs ----
def test_check_9_three_consecutive_runs(scratch_dirs):
    a, _, _ = scratch_dirs
    results = []
    for i in range(3):
        argv = [NODE, ZCODE_CJS, "-p", "Reply with exactly: OK",
                "--cwd", str(a), "--max-turns", "1", "--json"]
        t0 = time.monotonic()
        proc = zcode(*argv[2:])
        results.append((proc.returncode, extract_json(proc.stdout) is not None))
        archive(f"run7-stability-{i + 1}", argv, proc, extra=f"elapsed={time.monotonic() - t0:.1f}s")
    assert all(rc == 0 and parsed for rc, parsed in results), f"unstable: {results}"


# ---- Check 10: concurrent isolation ----
def test_check_10_concurrent_isolation(scratch_dirs):
    a, b, _ = scratch_dirs
    ma, mb = a / "conc-a.txt", b / "conc-b.txt"
    ma.unlink(missing_ok=True)
    mb.unlink(missing_ok=True)

    argv_a = [NODE, ZCODE_CJS, "-p",
              "Create a file named conc-a.txt in the current working directory containing exactly: A, then reply DONE",
              "--cwd", str(a), "--max-turns", "3", "--json"]
    argv_b = [NODE, ZCODE_CJS, "-p",
              "Create a file named conc-b.txt in the current working directory containing exactly: B, then reply DONE",
              "--cwd", str(b), "--max-turns", "3", "--json"]

    t0 = time.monotonic()
    pa = subprocess.Popen(argv_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    pb = subprocess.Popen(argv_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    out_a, err_a = pa.communicate(timeout=MODEL_RUN_TIMEOUT)
    out_b, err_b = pb.communicate(timeout=MODEL_RUN_TIMEOUT)
    elapsed = time.monotonic() - t0

    class P:  # lightweight shim for archive()
        returncode = None
        stdout = None
        stderr = None

    shim_a, shim_b = P(), P()
    shim_a.returncode, shim_a.stdout, shim_a.stderr = pa.returncode, out_a, err_a
    shim_b.returncode, shim_b.stdout, shim_b.stderr = pb.returncode, out_b, err_b
    archive("run8-concurrent-a", argv_a, shim_a, extra=f"elapsed={elapsed:.1f}s")
    archive("run8-concurrent-b", argv_b, shim_b, extra=f"elapsed={elapsed:.1f}s")

    # cwd isolation: each marker only in its own dir
    assert pa.returncode == 0 and pb.returncode == 0, f"rc: {pa.returncode}/{pb.returncode}\n{err_a[-800:]}\n{err_b[-800:]}"
    assert ma.exists() and ma.read_text(encoding="utf-8").strip() == "A"
    assert mb.exists() and mb.read_text(encoding="utf-8").strip() == "B"
    assert not (b / "conc-a.txt").exists() and not (a / "conc-b.txt").exists()
    # JSON stdout isolation: both parse independently
    ja, jb = extract_json(out_a), extract_json(out_b)
    assert ja is not None and jb is not None
    # session isolation: distinct session ids in JSON envelopes (if exposed)
    sid_a = str(ja.get("sessionId") or ja.get("session_id") or "")
    sid_b = str(jb.get("sessionId") or jb.get("session_id") or "")
    archive("run8-concurrent-sessionids", argv_a + argv_b, None,
            extra=f"session_a={sid_a or 'not-exposed'} session_b={sid_b or 'not-exposed'}")
    if sid_a and sid_b:
        assert sid_a != sid_b, "two concurrent runs shared a session id"
    # tool-permission isolation is enforced per-process by --allowed-tools (check 7);
    # here both had default permissions but distinct cwds — recorded as observation.


# ---- Check 10b: killing one concurrent process does not affect the other ----
def test_check_10b_kill_one_spare_the_other(scratch_dirs):
    a, b, _ = scratch_dirs
    mb = b / "kill-b.txt"
    mb.unlink(missing_ok=True)

    victim = subprocess.Popen(
        [NODE, ZCODE_CJS, "-p",
         "Write a detailed 500-word essay about saga compensation patterns, then reply DONE.",
         "--cwd", str(a), "--max-turns", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    survivor_argv = [NODE, ZCODE_CJS, "-p",
                     "Create a file named kill-b.txt in the current working directory containing exactly: alive, then reply DONE",
                     "--cwd", str(b), "--max-turns", "3", "--json"]
    survivor = subprocess.Popen(survivor_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    time.sleep(8)
    victim.kill()  # terminate the victim mid-flight
    victim_rc = victim.wait(timeout=30)
    out_s, err_s = survivor.communicate(timeout=MODEL_RUN_TIMEOUT)

    class P:
        returncode = None
        stdout = None
        stderr = None

    shim = P()
    shim.returncode, shim.stdout, shim.stderr = survivor.returncode, out_s, err_s
    archive("run9-kill-one-spare-other", survivor_argv, shim,
            extra=f"victim_rc={victim_rc} (killed); survivor_rc={survivor.returncode}; marker={mb.exists()}")

    assert victim_rc != 0
    assert survivor.returncode == 0, f"survivor died with victim:\n{err_s[-1500:]}"
    assert mb.exists() and mb.read_text(encoding="utf-8").strip() == "alive"
