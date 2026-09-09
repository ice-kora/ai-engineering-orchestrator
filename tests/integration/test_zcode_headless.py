"""P0-ZCODE-HEADLESS: readiness probe, flag-acceptance matrix, and the 10-check suite.

Empirical status (2026-09-09, evidence in docs/p0-evidence/zcode-headless/):
    DISCOVERED → runtime BLOCKED on model-provider wiring.

    * ZCode CLI v0.16.5 at D:/Software/zcode/install/ZCode/resources/glm/zcode.cjs
      exposes a headless surface in --help (-p/--print, --prompt, --cwd, --mode,
      --max-turns, --allowed-tools, --resume, --json, ...).
    * The actual argument parser ACCEPTS --prompt/--json/--cwd/-p but REJECTS
      --max-turns and --settings ("Unknown option"), i.e. help/parser drift.
    * Once parsing passes, headless exits with
      "Model config is missing. Create ~/.zcode/cli/config.json with an explicit
      model provider before running ZCode."
      Adding {"model": {"main": {"providerId": "zai", "modelId": "glm-5.3"}}}
      does NOT satisfy it (tested then reverted; backup restored). The official
      docs do not document CLI headless or its config schema. Likely unblock:
      `zcode login` (Z.AI OAuth) or provider credentials in modelProviderOptions
      — escalated to GPT for arbitration, NOT solved by guessing.

Therefore:
    * test_flag_acceptance_matrix and test_cli_presence run always (real evidence).
    * The 9+1 verification checks SKIP with reason BLOCKED_MODEL_CONFIG until the
      probe succeeds; once unblocked (e.g. after `zcode login`), re-run this
      module to execute them for real. VERDICT ZCODE_HEADLESS = VERIFIED only
      when all 9+1 pass (GPT Final Gate supplement #5).
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

MODEL_RUN_TIMEOUT = 240

MODEL_CONFIG_MISSING = "Model config is missing"


def zcode(*args: str, timeout: int = MODEL_RUN_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, ZCODE_CJS, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def archive(name: str, argv: list[str], proc: subprocess.CompletedProcess | None, extra: str = "") -> None:
    # GPT security hotfix: never persist the local username into repo evidence
    import getpass
    _u = getpass.getuser()

    def _clean(s):
        return s.replace("C:\\Users\\" + _u, "C:\\Users\\<user>") if isinstance(s, str) else s

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    body = [
        f"$ {_clean(' '.join(map(str, argv)))}",
        f"exit={getattr(proc, 'returncode', 'N/A')}",
        "--- stdout ---", _clean(proc.stdout if proc else "N/A"),
        "--- stderr ---", _clean(proc.stderr if proc else "N/A"),
    ]
    if extra:
        body += ["--- extra ---", _clean(extra)]
    (EVIDENCE_DIR / f"{name}.txt").write_text("\n".join(body), encoding="utf-8")


def extract_json(text: str) -> dict | None:
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


@pytest.fixture(scope="module")
def headless_ready(scratch_dirs):
    """Probe once: is the CLI able to run a headless prompt end-to-end?

    Returns the CompletedProcess on success; raises pytest.skip when blocked.
    """
    a, _, _ = scratch_dirs
    argv = [NODE, ZCODE_CJS, "--prompt", "Reply with exactly: OK",
            "--json", "--cwd", str(a)]
    proc = zcode(*argv[2:])
    if proc.returncode == 0 and extract_json(proc.stdout) is not None:
        archive("probe-ready", argv, proc, extra="HEADLESS READY")
        return proc
    if MODEL_CONFIG_MISSING in proc.stderr:
        archive("probe-blocked", argv, proc,
                extra="BLOCKED: model provider wiring missing; see module docstring")
        pytest.skip(
            "BLOCKED_MODEL_CONFIG: headless CLI requires an explicit model provider "
            "config/credentials (undocumented). Unblock candidates: `zcode login` "
            "(Z.AI OAuth) or provider credentials in config.json; escalated to GPT. "
            f"Raw evidence: {EVIDENCE_DIR / 'probe-blocked.txt'}"
        )
    archive("probe-failed", argv, proc, extra="unexpected failure mode")
    pytest.fail(f"unexpected headless failure: exit={proc.returncode}\n{proc.stderr[-1500:]}")


# ---------- always-on evidence tests (no model cost) ----------

def test_cli_presence_and_version():
    proc = zcode("--version", timeout=60)
    archive("version", [NODE, ZCODE_CJS, "--version"], proc)
    assert proc.returncode == 0 and "0.16" in proc.stdout


def test_flag_acceptance_matrix(scratch_dirs):
    """Parse-level capability matrix: which --help flags the parser really accepts.

    A flag is ACCEPTED if the CLI gets past argument parsing (fails later with
    the model-config error instead of 'Unknown option'). Runs cost no tokens.
    """
    a, _, _ = scratch_dirs
    prompt = "Reply with exactly: OK"
    cases = {
        "--prompt <text>": ["--prompt", prompt],
        "-p (positional)": ["-p", prompt],
        "--json": ["--json", "--prompt", prompt],
        "--cwd <path>": ["--cwd", str(a), "--prompt", prompt],
        "--mode yolo": ["--mode", "yolo", "--prompt", prompt],
        "--max-turns <n>": ["--max-turns", "1", "--prompt", prompt],
        "--allowed-tools <list>": ["--allowed-tools", "Read", "--prompt", prompt],
        "--disallowed-tools <list>": ["--disallowed-tools", "Write", "--prompt", prompt],
        "--settings <path>": ["--settings", str(SCRATCH / "nope.json"), "--prompt", prompt],
    }
    matrix: dict[str, bool] = {}
    for label, args in cases.items():
        proc = zcode(*args, timeout=90)
        unknown = "Unknown option" in (proc.stderr or "")
        matrix[label] = not unknown
        archive(f"flag-{label.split(' ')[0].lstrip('-') or 'positional'}", [NODE, ZCODE_CJS, *args], proc,
                extra=f"accepted={not unknown}")
    summary = "\n".join(f"{k}: {'ACCEPTED' if v else 'REJECTED'}" for k, v in matrix.items())
    archive("flag-matrix-summary", ["<matrix>"], None, extra=summary)
    # hard evidence for the report: the drift flags
    assert matrix["--prompt <text>"] and matrix["--json"] and matrix["--cwd <path>"]
    # document (not assert) drift flags — they are findings, not regressions


def test_exit_code_reliability_bad_args():
    argv = [NODE, ZCODE_CJS, "--definitely-not-a-real-flag"]
    proc = zcode(*argv[2:], timeout=60)
    archive("exit-bad-args", argv, proc)
    assert proc.returncode != 0, "invalid flags must fail loudly (non-zero exit)"


# ---------- 9+1 verification checks (skip while BLOCKED_MODEL_CONFIG) ----------

def test_check_1_2_3_5_minimal_cwd_json_streams(headless_ready, scratch_dirs):
    a, _, _ = scratch_dirs
    marker = a / "cwd-marker.txt"
    marker.unlink(missing_ok=True)
    argv = [NODE, ZCODE_CJS, "--prompt",
            "Create a file named cwd-marker.txt in the current working directory "
            "containing exactly the word ok, then reply with exactly: DONE",
            "--json", "--cwd", str(a)]
    t0 = time.monotonic()
    proc = zcode(*argv[2:])
    archive("run1-minimal-cwd-json", argv, proc,
            extra=f"elapsed={time.monotonic() - t0:.1f}s; marker_exists={marker.exists()}")

    assert proc.returncode == 0, f"exit={proc.returncode}\nstderr:\n{proc.stderr[-2000:]}"
    assert marker.exists(), "cwd-marker.txt not created => --cwd not honored or task failed"
    assert marker.read_text(encoding="utf-8").strip() == "ok"
    parsed = extract_json(proc.stdout)
    assert parsed is not None and isinstance(parsed, dict), (
        f"--json stdout not machine-parseable:\n{proc.stdout[:1000]}"
    )
    assert isinstance(proc.stderr, str)


def test_check_6_timeout_kill_is_safe(headless_ready, scratch_dirs):
    _, b, _ = scratch_dirs
    argv = [NODE, ZCODE_CJS, "--prompt",
            "Write a detailed 500-word essay about distributed leases, then reply DONE.",
            "--cwd", str(b)]
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        zcode(*argv[2:], timeout=8)
    elapsed = time.monotonic() - t0
    archive("run4-timeout-kill", argv, None, extra=f"TimeoutExpired after {elapsed:.1f}s (limit 8s)")
    assert elapsed < 60, "process did not terminate in reasonable time after kill"


def test_check_7_allowed_tools_restriction(headless_ready, scratch_dirs):
    _, _, c = scratch_dirs
    marker = c / "blocked-marker.txt"
    marker.unlink(missing_ok=True)
    argv = [NODE, ZCODE_CJS, "--prompt",
            "Create a file named blocked-marker.txt in the current working directory "
            "containing ok, then reply DONE.",
            "--json", "--cwd", str(c), "--allowed-tools", "Read"]
    proc = zcode(*argv[2:])
    archive("run5-allowed-tools", argv, proc, extra=f"marker_exists={marker.exists()} (must be False)")
    assert proc.returncode == 0, "restriction test requires a successful run, not a crashed one"
    assert not marker.exists(), "write happened despite --allowed-tools Read => restriction not enforced"


def test_check_8_runs_inside_worktree(headless_ready):
    wt = DEMO_REPO / "worktrees" / "agent-zcode"
    listed = subprocess.run(
        ["git", "-C", str(DEMO_REPO), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if wt.as_posix() not in listed.stdout:
        subprocess.run(["git", "-C", str(DEMO_REPO), "worktree", "add", "-b",
                        "agent/zcode/home", str(wt), "main"],
                       capture_output=True, text=True, check=True)
    argv = [NODE, ZCODE_CJS, "--prompt", "Reply with exactly: OK",
            "--json", "--cwd", str(wt)]
    proc = zcode(*argv[2:])
    archive("run6-inside-worktree", argv, proc, extra=f"cwd={wt}")
    assert proc.returncode == 0, f"stderr:\n{proc.stderr[-2000:]}"
    assert extract_json(proc.stdout) is not None


def test_check_9_three_consecutive_runs(headless_ready, scratch_dirs):
    a, _, _ = scratch_dirs
    results = []
    for i in range(3):
        argv = [NODE, ZCODE_CJS, "--prompt", "Reply with exactly: OK",
                "--json", "--cwd", str(a)]
        t0 = time.monotonic()
        proc = zcode(*argv[2:])
        results.append((proc.returncode, extract_json(proc.stdout) is not None))
        archive(f"run7-stability-{i + 1}", argv, proc, extra=f"elapsed={time.monotonic() - t0:.1f}s")
    assert all(rc == 0 and parsed for rc, parsed in results), f"unstable: {results}"


def test_check_10_concurrent_isolation(headless_ready, scratch_dirs):
    a, b, _ = scratch_dirs
    ma, mb = a / "conc-a.txt", b / "conc-b.txt"
    ma.unlink(missing_ok=True)
    mb.unlink(missing_ok=True)

    argv_a = [NODE, ZCODE_CJS, "--prompt",
              "Create a file named conc-a.txt in the current working directory containing exactly: A, then reply DONE",
              "--json", "--cwd", str(a)]
    argv_b = [NODE, ZCODE_CJS, "--prompt",
              "Create a file named conc-b.txt in the current working directory containing exactly: B, then reply DONE",
              "--json", "--cwd", str(b)]

    t0 = time.monotonic()
    pa = subprocess.Popen(argv_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    pb = subprocess.Popen(argv_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    out_a, err_a = pa.communicate(timeout=MODEL_RUN_TIMEOUT)
    out_b, err_b = pb.communicate(timeout=MODEL_RUN_TIMEOUT)
    elapsed = time.monotonic() - t0

    class P:
        pass

    shim_a, shim_b = P(), P()
    shim_a.returncode, shim_a.stdout, shim_a.stderr = pa.returncode, out_a, err_a
    shim_b.returncode, shim_b.stdout, shim_b.stderr = pb.returncode, out_b, err_b
    archive("run8-concurrent-a", argv_a, shim_a, extra=f"elapsed={elapsed:.1f}s")
    archive("run8-concurrent-b", argv_b, shim_b, extra=f"elapsed={elapsed:.1f}s")

    assert pa.returncode == 0 and pb.returncode == 0, f"rc: {pa.returncode}/{pb.returncode}\n{err_a[-800:]}\n{err_b[-800:]}"
    assert ma.exists() and ma.read_text(encoding="utf-8").strip() == "A"
    assert mb.exists() and mb.read_text(encoding="utf-8").strip() == "B"
    assert not (b / "conc-a.txt").exists() and not (a / "conc-b.txt").exists()
    ja, jb = extract_json(out_a), extract_json(out_b)
    assert ja is not None and jb is not None
    sid_a = str(ja.get("sessionId") or ja.get("session_id") or "")
    sid_b = str(jb.get("sessionId") or jb.get("session_id") or "")
    archive("run8-concurrent-sessionids", argv_a + argv_b, None,
            extra=f"session_a={sid_a or 'not-exposed'} session_b={sid_b or 'not-exposed'}")
    if sid_a and sid_b:
        assert sid_a != sid_b, "two concurrent runs shared a session id"


def test_check_10b_kill_one_spare_the_other(headless_ready, scratch_dirs):
    a, b, _ = scratch_dirs
    mb = b / "kill-b.txt"
    mb.unlink(missing_ok=True)

    victim = subprocess.Popen(
        [NODE, ZCODE_CJS, "--prompt",
         "Write a detailed 500-word essay about saga compensation patterns, then reply DONE.",
         "--cwd", str(a)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    survivor_argv = [NODE, ZCODE_CJS, "--prompt",
                     "Create a file named kill-b.txt in the current working directory containing exactly: alive, then reply DONE",
                     "--json", "--cwd", str(b)]
    survivor = subprocess.Popen(survivor_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    time.sleep(8)
    victim.kill()
    victim_rc = victim.wait(timeout=30)
    out_s, err_s = survivor.communicate(timeout=MODEL_RUN_TIMEOUT)

    class P:
        pass

    shim = P()
    shim.returncode, shim.stdout, shim.stderr = survivor.returncode, out_s, err_s
    archive("run9-kill-one-spare-other", survivor_argv, shim,
            extra=f"victim_rc={victim_rc} (killed); survivor_rc={survivor.returncode}; marker={mb.exists()}")

    assert victim_rc != 0
    assert survivor.returncode == 0, f"survivor died with victim:\n{err_s[-1500:]}"
    assert mb.exists() and mb.read_text(encoding="utf-8").strip() == "alive"
