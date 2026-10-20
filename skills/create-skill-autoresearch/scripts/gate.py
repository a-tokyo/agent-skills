#!/usr/bin/env python3
"""Run the Phase 5.0 eval gate end to end and write its canonical artifact.

The gate runs as the owned job `gate` (run_owned.py; record and log in work/pids/), whether you
start it with this script or with `run_owned.py start gate -- python3 gate.py ...`. A full gate can
take over an hour, longer than an agent's shell call waits, so start it detached, then block on it:

    gate.py --detach --model <model> [options]          # starts the job, prints how to wait for it
    gate.py --wait [--wait-timeout 540]                 # blocks; 124 = still running: call --wait again
    gate.py --restart --model <model> [options]         # stops only the recorded `gate` group, starts it detached
    gate.py --stop                                      # stops it (and the eval runs it started)

--wait blocks on the recorded `gate` job for up to --wait-timeout seconds (default 540, under the 600 s a
Claude Code shell call may wait) and never starts one; with --detach or --restart it starts the gate first,
then blocks. When the gate has finished it prints the gate's log and exits with the gate's exit code (an
already finished gate returns at once). At the timeout it prints one progress line (elapsed time and the
log's last line) and exits 124: call --wait again rather than polling `run_owned.py status`. Exit 3: no
`gate` job is recorded; 125: the job ended without recording an exit status.

Without --detach it starts the job and waits for it (for humans at a terminal). If that call is cut
off, the job keeps running. A second start while a `gate` job is live is refused. --detach and
--restart first check the preconditions synchronously (the suite, the recorded cases, each case's
`scaffold_script`, run-case.sh and --executor in manual mode) and exit 3 without starting anything,
or stopping a running gate, when one is missing.

Mode (--backend): `plugin` runs Claude Code's `claude plugin eval` and needs it (`--help` exits 0) and a
model-invoked skill; `manual` runs run-case.sh and never calls `claude`. The default is `plugin` only
when the build runs inside Claude Code ($CLAUDECODE is set), the skill is model-invoked and the probe
exits 0, else `manual`: a build on another host targets that host's model, which plugin eval cannot run,
even when `claude` is installed. Inside Claude Code, `--backend manual` on a model-invoked skill that plugin
eval can run is refused (exit 3): it costs a headless session per case, arm and run. Only a build on another
host whose shell inherits $CLAUDECODE passes `--backend manual --force-manual "<host>: <reason>"`; the reason
is logged in gate.json (`force_manual`) and data-split.yaml. The case list is never typed by hand: `gate.py record` (3.6) writes `plugin_eval` and
`plugin_eval_available` into work/evaluation/data-split.yaml from disk, and every gate run records
again before it starts, so the list always matches what runs:

  plugin  every case directory under work/evaluation/plugin/evals/, after a zero-cost load check
          (`claude plugin eval . --max-cost-usd 0`) that fails on any case file plugin eval cannot
          load — it would skip that case and the artifact would miss it
  manual  the gate gold standards from the split: `test` with 10 or more gold standards, every gold
          standard with 3-9 (a `test` split there is an error); run-case.sh gets each entry as its <case>

    gate.py record [--workspace DIR] [--skill NAME] [--backend plugin|manual [--force-manual REASON]]
                                    # exit 0 recorded, 3 precondition missing

Each eval run goes in its own owned process group, stopped on timeout.

  plugin  refresh work/evaluation/plugin/skills/<skill>/ from output/<skill>/, then run
          `claude plugin eval .` with and without the skill -> work/evaluation/plugin-eval.json
  manual  call work/evaluation/run-case.sh <case> <with|without> --runs times per arm per case;
          it prints EXECUTOR=$GATE_EXECUTOR and SCORE=<0..1> (the last of each counts; a run without the
          --executor it was given fails) -> work/evaluation/uplift.json
          (a failed run is retried once; a second failure leaves the artifact partial). A complete
          without-skill arm is cached in work/evaluation/baseline-cache/<key>.json, the key a sha256 of the
          case, model, executor, run-case.sh, the case's suite directory and every gate input (not the
          skill, which the bare arm never reads); a later gate reuses it, recording the key and file hash
          per case in uplift.json for check_gate.py, unless --fresh-baseline. Plugin mode caches nothing:
          `claude plugin eval --ablation` offers none | with-without, no without-only arm.

At the start it records the mode, the probe's exit code and sha256 hashes of output/<skill>/, the case set,
the data-split.yaml splits, run-case.sh and the scorer and fixture files (gate_common.inputs_sha256) in
work/evaluation/gate.json; at the end it hashes them again, and a change while the gate ran leaves no
finished run (exit 1). The finished record adds the artifact's hash; check_gate.py fails once any of them
changes. It moves a previous artifact
aside to <name>.prev.json before running so a stale file can never pass, ends by running
check_gate.py, and writes nothing outside work/evaluation/ and work/pids/.

    gate.py --model <model> [--workspace DIR] [--skill NAME] [--backend plugin|manual [--force-manual REASON]]
            [--runs 3] [--jobs 4]
            [--allow-tools Bash Write] [--in-sample] [--executor single-call|agentic] [--fresh-baseline]
            [--timeout 5400] [--case-timeout 1200] [--detach] [--restart]
    gate.py --stop [--workspace DIR]

run-case.sh receives GATE_MODEL, GATE_RUN and GATE_EXECUTOR in its environment and runs from the workspace root; on
Windows the gate runs it with Git for Windows' bash (CLAUDE_CODE_GIT_BASH_PATH, else bash on PATH or the
default Git install; WSL's bash.exe is skipped). Tested on macOS, Linux and Windows.

Exit codes: 0 complete and valid (--detach, --restart, --stop: started or stopped); 1 the gate ran but the artifact is
missing, partial or invalid (the message says why); 2 usage error; 3 a precondition is missing
(suite, cases, run-case.sh), a `gate` job is already running, or (--wait) none is recorded; 124 (--wait) still
running at --wait-timeout; 125 (--wait) the job ended without an exit status; 143 the gate was stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
import check_gate  # noqa: E402  (sibling imports after disabling bytecode)
import run_owned  # noqa: E402
from gate_common import (  # noqa: E402
    BASELINE_CACHE, DATA_SPLIT, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, PLUGIN_DIR, REQUIRED_RUNS, RUN_CASE,
    GateError, baseline_key, baseline_path, cases_sha256, check_skill_name, file_sha256, inputs_sha256,
    model_invoked, omitted_gold, read_baseline, read_data_split, resolve_skill,
    log_attempt, skill_sha256, split_sha256, utf8_stdio, write_gate_cases, write_json,
)

EXIT_INVALID = 1
EXIT_PRECONDITION = 3
EXIT_STOPPED = 143
JOB = "gate"
# `claude plugin eval --help` answers in seconds; a minute bounds a CLI hung on a first-run update or login.
PROBE_TIMEOUT = 60
# How often the gate checks its runs and the stop request: a prompt stop without a busy loop.
POLL_SECONDS = 0.5
# --stop/--restart grace for the gate job: CHILD_GRACE for its eval runs, plus time to write its record.
STOP_GRACE = 30
# An eval run's time to exit after TERM before KILL; a run being stopped only needs to flush its log.
CHILD_GRACE = 10
# The default --wait timeout: under the 600 s a Claude Code Bash call may wait, so --wait returns
# (exit 124) before the host cuts the call off.
WAIT_SECONDS = 540
SCORE_RE = re.compile(r"^SCORE=(\S+)\s*$", re.MULTILINE)
EXECUTOR_RE = re.compile(r"^EXECUTOR=(\S+)\s*$", re.MULTILINE)
# Plugin eval runs Claude models only; a model id naming none of these is likely another host's.
CLAUDE_MODEL_RE = re.compile(r"claude|anthropic|sonnet|opus|haiku", re.IGNORECASE)
SCAFFOLD_RE = re.compile(r"^\s*scaffold_script:\s*(\S.*)$")
NAME_RE = re.compile(r"^name:\s*(\S.*)$")
# The zero-cost load check parses every case file and stops before any run; two minutes covers a large
# suite on a cold CLI.
LOAD_CHECK_TIMEOUT = 120
NOT_PROBED = "not-probed"
# One retry absorbs a transient failure (rate limit, dropped connection); a second failure in a row is
# reported, leaving the artifact partial rather than retrying it out of sight.
ATTEMPTS = 2
# Concurrent eval runs: four keeps a suite moving while staying under a typical API account's rate limit.
DEFAULT_JOBS = 4
# Defaults for a whole plugin eval (90 minutes) and one manual run (20 minutes, an agentic run included):
# well past a healthy run, so a run that reaches them is hung and is stopped, not waited on.
EVAL_TIMEOUT = 5400
CASE_TIMEOUT = 1200
SCRIPTS = Path(__file__).resolve().parent

# Eval runs this gate job started and has not finished waiting on; TERM stops them before exiting.
_ACTIVE: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()
_STOP = threading.Event()


class GateStopped(Exception):
    """The gate job received SIGTERM (run_owned.py stop gate, or gate.py --stop / --restart)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _claude() -> str | None:
    """The claude executable on PATH; on Windows npm installs it as claude.cmd, which only PATHEXT finds."""
    return shutil.which("claude")


def _git_bash() -> str | None:
    """Windows: Git for Windows' bash.exe, never WSL's launcher in System32."""
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"), shutil.which("bash")]
    candidates += [str(Path(os.environ[var]).joinpath(*rest)) for var, *rest in (
        ("ProgramFiles", "Git", "bin", "bash.exe"), ("ProgramFiles(x86)", "Git", "bin", "bash.exe"),
        ("LOCALAPPDATA", "Programs", "Git", "bin", "bash.exe")) if os.environ.get(var)]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and Path(candidate).parent.resolve() != system32.resolve():
            return candidate
    return None


def _adapter_argv(ws: Path) -> list[str]:
    adapter = (ws / RUN_CASE).resolve()
    if os.name != "nt":
        return [str(adapter)]
    bash = _git_bash()
    if bash is None:
        raise GateError(f"manual mode on Windows runs {RUN_CASE} with Git for Windows' bash, and none was found; "
                        "install Git for Windows or set CLAUDE_CODE_GIT_BASH_PATH to its bash.exe")
    return [bash, adapter.as_posix()]


def probe_plugin_eval() -> int:
    """Exit code of `claude plugin eval --help`: 127 if claude is missing, 124 on timeout."""
    claude = _claude()
    if claude is None:
        return 127
    try:
        return subprocess.run([claude, "plugin", "eval", "--help"], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=PROBE_TIMEOUT).returncode
    except FileNotFoundError:
        return 127
    except subprocess.TimeoutExpired:
        return 124


def _set_aside(path: Path) -> None:
    if path.exists():
        os.replace(path, path.with_name(path.stem + ".prev.json"))


def _is_case(path: Path) -> bool:
    return path.is_dir() and ((path / "case.yaml").is_file() or (path / "prompt.md").is_file())


def _case_yaml_name(case_yaml: Path) -> str | None:
    for line in case_yaml.read_text(encoding="utf-8", errors="replace").splitlines():
        match = NAME_RE.match(line)
        if match:
            return match.group(1).split(" #")[0].strip().strip("\"'") or None
    return None


def suite_cases(ws: Path) -> list[str]:
    """The case directories under plugin/evals/, as claude plugin eval runs them: one directory per
    case, named like its case.yaml `name:`. Raises GateError on a layout it would run differently."""
    plugin = ws / PLUGIN_DIR
    evals = plugin / "evals"
    if not (plugin / ".claude-plugin" / "plugin.json").is_file() or not evals.is_dir():
        raise GateError(f"{PLUGIN_DIR} needs .claude-plugin/plugin.json and evals/ (scaffold the suite in 3.6)")
    cases = sorted(p.name for p in evals.iterdir() if _is_case(p))
    nested = sorted({p.parent.relative_to(evals).as_posix() for marker in ("case.yaml", "prompt.md")
                     for p in evals.rglob(marker)
                     if len(p.relative_to(evals).parts) > 2
                     and p.relative_to(evals).parts[0] not in (*cases, "results")})
    if nested:
        raise GateError(f"nested eval cases {nested}: keep one directory per case directly under evals/")
    renamed = [f"{c} (name: {n})" for c in cases if (c_yaml := evals / c / "case.yaml").is_file()
               and (n := _case_yaml_name(c_yaml)) not in (None, c)]
    if renamed:
        raise GateError(f"case.yaml `name:` differs from its directory: {', '.join(renamed)}; rename one")
    if not cases:
        raise GateError(f"no eval cases under {PLUGIN_DIR / 'evals'} (scaffold them in 3.6)")
    return cases


def load_check(ws: Path) -> None:
    """Load every case file with a zero cost ceiling: claude plugin eval reports each case file it cannot
    load (a `✗` line) and stops before the first run, spending nothing. A case that fails to load is
    otherwise skipped silently and the artifact misses it. Fails only on reported load errors."""
    with tempfile.TemporaryDirectory(prefix="gate-load-check-") as tmp:
        cmd = [_claude() or "claude", "plugin", "eval", ".", "--max-cost-usd", "0", "--ablation", "none", "--trust-plugin",
               "--no-publish", "--output-dir", tmp, "--report", str(Path(tmp) / "report.html"),
               "--json", str(Path(tmp) / "load-check.json")]
        try:
            out = subprocess.run(cmd, cwd=ws / PLUGIN_DIR, stdin=subprocess.DEVNULL, capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=LOAD_CHECK_TIMEOUT)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            print(f"gate: load check skipped ({exc})", file=sys.stderr)
            return
    failed = [line[2:].strip() for line in (out.stdout + out.stderr).splitlines() if line.startswith("✗ ")]
    if failed:
        raise GateError(f"{len(failed)} case file(s) fail to load, so claude plugin eval would skip them; fix "
                        "and re-run:\n  " + "\n  ".join(failed))
    if out.returncode not in (0, 1, 2):
        print(f"gate: load check exited {out.returncode} without naming a case; continuing", file=sys.stderr)


def default_backend() -> str:
    """`plugin` inside Claude Code, which sets $CLAUDECODE in the shells it runs; `manual` elsewhere."""
    return "plugin" if os.environ.get("CLAUDECODE") else "manual"


def pick_mode(invoked: bool, backend: str | None, force_manual: str | None = None) -> tuple[str, int | str, str]:
    """(mode, probe exit code or NOT_PROBED, why) for the --backend asked for (None: default_backend).
    Raises GateError when --backend plugin is asked for and cannot run, and when --backend manual is asked
    for inside Claude Code on a model-invoked skill that plugin eval can run, unless --force-manual gives
    a reason: there the manual protocol only costs more (a headless session per case, arm and run)."""
    asked = backend or default_backend()
    if asked == "manual" and force_manual:
        return "manual", NOT_PROBED, f"--backend manual --force-manual \"{force_manual}\"; claude not probed"
    if asked == "manual" and backend and invoked and os.environ.get("CLAUDECODE"):
        available = probe_plugin_eval()
        if available == 0:
            raise GateError("--backend manual inside Claude Code: this skill is model-invoked and `claude plugin "
                            "eval` runs, so the gate is plugin mode; drop --backend manual. Only a build on "
                            "another host whose shell inherits $CLAUDECODE passes --backend manual --force-manual "
                            "\"<that host>: <why>\" (logged in gate.json and data-split.yaml)")
        return "manual", available, "--backend manual: exit code of `claude plugin eval --help`"
    if asked == "manual":
        why = "--backend manual" if backend else "not inside Claude Code ($CLAUDECODE unset)"
        return "manual", NOT_PROBED, f"{why}: the manual protocol, claude not probed"
    if not invoked:
        if backend:
            raise GateError("--backend plugin: a user-invoked skill never fires through the Skill tool; use manual")
        return "manual", NOT_PROBED, "user-invoked skill: always the manual protocol, claude not probed"
    available = probe_plugin_eval()
    if available != 0 and backend:
        raise GateError(f"--backend plugin needs `claude plugin eval` (`claude plugin eval --help` exited {available})")
    return ("plugin" if available == 0 else "manual"), available, "exit code of `claude plugin eval --help`"


def record_cases(ws: Path, skill: str, backend: str | None = None,
                 force_manual: str | None = None) -> tuple[list[str], str, int | str, bool]:
    """Write the gate's case list into data-split.yaml from what is on disk: (cases, mode, probe exit
    code, model-invoked). Plugin mode lists the suite's case directories after a load check; manual mode
    lists the gate gold standards from the split (3.6) and never calls `claude`."""
    invoked = model_invoked(ws / "output" / skill / "SKILL.md")
    mode, available, why = pick_mode(invoked, backend, force_manual)
    if mode == "plugin":
        cases, source = suite_cases(ws), (PLUGIN_DIR / "evals").as_posix()
        load_check(ws)
    else:
        split = read_data_split(ws / DATA_SPLIT)
        if split.gold_error:
            raise GateError(f"{DATA_SPLIT}: {split.gold_error}")
        cases, source = split.gate_gold(), "the gold standards in this file"
        if not cases:
            raise GateError(f"{DATA_SPLIT} lists no gold standards (all_cases, training, validation or test; "
                            "4.2); the manual protocol runs one gate case per gold standard")
        omitted = omitted_gold(ws, split.all_gold())
        if omitted:
            raise GateError(f"{DATA_SPLIT} leaves out gold standards found on disk: {', '.join(omitted)}; list each "
                            "under all_cases, training, validation or test (4.2), or move a file that is not a "
                            "gold standard out of that folder")
    write_gate_cases(ws / DATA_SPLIT, cases, available, source, why)
    return cases, mode, available, invoked


def run_owned_job(sdir: Path, name: str, cmd: list[str], cwd: Path, timeout: float,
                  env: dict[str, str] | None = None) -> tuple[int | None, str]:
    """Run one owned job to completion; returns (exit code or None on timeout, log path).
    Raises GateStopped once the gate has been asked to stop, leaving the job for _stop_children."""
    with _LOCK:
        if _STOP.is_set():
            raise GateStopped()
        proc = run_owned.start(sdir, name, cmd, cwd=cwd, env=env)
        _ACTIVE[name] = proc
    log = run_owned.load(sdir, name).log
    deadline = time.monotonic() + timeout
    while True:
        rc = run_owned.wait(sdir, name, timeout=max(0.0, min(POLL_SECONDS, deadline - time.monotonic())), proc=proc)
        if rc != run_owned.EXIT_TIMEOUT:
            break
        if _STOP.is_set():
            raise GateStopped()
        if time.monotonic() >= deadline:
            run_owned.stop(sdir, name, grace=CHILD_GRACE, proc=proc)
            rc = None
            break
    with _LOCK:
        _ACTIVE.pop(name, None)
    return rc, log


def _stop_children(sdir: Path) -> None:
    with _LOCK:
        _STOP.set()
        jobs = list(_ACTIVE.items())
    if not jobs:
        return
    try:
        for outcome in run_owned.stop_many(sdir, jobs, grace=CHILD_GRACE):
            print(f"gate: {outcome}", file=sys.stderr)
    except run_owned.OwnedError as exc:
        print(f"gate: could not stop its eval runs: {exc}", file=sys.stderr)


def _scaffold_script(case_yaml: Path) -> str | None:
    """The `scaffold_script:` value in a case.yaml (the only key read; quotes and comments stripped)."""
    for line in case_yaml.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SCAFFOLD_RE.match(line)
        if match:
            return match.group(1).split(" #")[0].strip().strip("\"'") or None
    return None


def check_scaffolds(ws: Path, cases: list[str]) -> None:
    missing = []
    for case in cases:
        case_dir = ws / PLUGIN_DIR / "evals" / case
        script = _scaffold_script(case_dir / "case.yaml") if (case_dir / "case.yaml").is_file() else None
        if script and not (case_dir / script).is_file():
            missing.append(f"{case}: {script}")
    if missing:
        raise GateError(f"scaffold_script not found (relative to the case directory): {', '.join(missing)}")


def check_adapter(ws: Path, args: argparse.Namespace) -> None:
    adapter = ws / RUN_CASE
    if not adapter.is_file() or not os.access(adapter, os.X_OK):
        raise GateError(f"{RUN_CASE} is missing or not executable; 3.6 writes it (chmod +x)")
    _adapter_argv(ws)
    if args.executor is None:
        raise GateError("manual mode: pass --executor single-call or agentic (recorded in uplift.json)")


def preconditions(ws: Path, args: argparse.Namespace) -> tuple[str, list[str], str, int, bool]:
    """Everything the gate needs before it runs: (skill, cases, mode, probe exit code, model-invoked).
    Re-records the case list from disk first, so the gate never runs a hand-edited list. Raises
    GateError naming the first missing piece."""
    skill = resolve_skill(ws, args.skill)
    cases, mode, available, invoked = record_cases(ws, skill, args.backend, args.force_manual)
    check_scaffolds(ws, cases)
    if mode == "manual":
        check_adapter(ws, args)
    elif not CLAUDE_MODEL_RE.search(args.model or ""):
        print(f"gate: warning: plugin mode runs Claude models, and --model {args.model!r} names none; a build on "
              "another host (launched from Claude Code, which exports $CLAUDECODE) passes --backend manual "
              "--force-manual '<host>: <reason>'",
              file=sys.stderr)
    return skill, cases, mode, available, invoked


def _skill_copy(ws: Path, skill: str) -> Path:
    """work/evaluation/plugin/skills/<skill>, refused when it would resolve anywhere else (a symlinked
    plugin/, skills/ or copy) since the gate deletes it before each run."""
    copy = ws.resolve() / PLUGIN_DIR / "skills" / check_skill_name(skill)
    if os.path.normcase(copy.resolve()) != os.path.normcase(copy):
        raise GateError(f"{copy} resolves to {copy.resolve()}, outside {PLUGIN_DIR / 'skills'}; the gate deletes and "
                        "rewrites that copy, so it refuses a symlink anywhere on the path")
    return copy


def run_plugin(ws: Path, skill: str, args: argparse.Namespace) -> None:
    plugin = ws / PLUGIN_DIR
    copy = _skill_copy(ws, skill)
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(ws / "output" / skill, copy)

    cmd = [_claude() or "claude", "plugin", "eval", ".", "--ablation", "with-without", "--runs", str(args.runs),
           "-j", str(args.jobs), "--model", args.model, "--allow-tools", *args.allow_tools,
           "--scaffold", "--trust-plugin", "--no-publish", "--threshold", "0",
           "--json", str((ws / PLUGIN_ARTIFACT).resolve())]
    rc, log = run_owned_job(run_owned.state_dir(ws), "gate-plugin-eval", cmd, plugin, args.timeout)
    if rc is None:
        print(f"gate: claude plugin eval timed out after {args.timeout:g}s and was stopped; log: {log}", file=sys.stderr)
    elif rc != 0:
        print(f"gate: claude plugin eval exited {rc}; log: {log}", file=sys.stderr)
    skipped = [line for line in Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
               if line.startswith("✗ ")]
    if skipped:
        print(f"gate: claude plugin eval could not load {len(skipped)} case file(s):\n  " + "\n  ".join(skipped),
              file=sys.stderr)


@dataclass
class RunResult:
    case: str
    arm: str
    run: int
    score: float | None
    error: str | None
    log: str


def _one_run(ws: Path, idx: int, case: str, arm: str, run: int, args: argparse.Namespace) -> RunResult:
    sdir = run_owned.state_dir(ws)
    env = {**os.environ, "GATE_MODEL": args.model, "GATE_RUN": str(run), "GATE_EXECUTOR": args.executor}
    error, log = "not run", ""
    for attempt in range(1, ATTEMPTS + 1):
        name = f"gate-c{idx:02d}-{arm}-r{run}-a{attempt}"
        rc, log = run_owned_job(sdir, name, [*_adapter_argv(ws), case, arm], ws, args.case_timeout, env)
        if rc is None:
            error = f"timed out after {args.case_timeout:g}s"
            continue
        text = Path(log).read_text(encoding="utf-8", errors="replace")
        found, executor = SCORE_RE.findall(text), EXECUTOR_RE.findall(text)
        if rc != 0:
            error = f"exit {rc}"
        elif not found:
            error = "no SCORE= line"
        elif executor[-1:] != [args.executor]:
            error = (f"no EXECUTOR={args.executor} line (run-case.sh echoes the $GATE_EXECUTOR it ran; "
                     f"got {executor[-1] if executor else 'none'})")
        else:
            try:
                score = float(found[-1])
            except ValueError:
                error = f"SCORE={found[-1]} is not a number"
                continue
            if 0 <= score <= 1:
                return RunResult(case, arm, run, score, None, log)
            error = f"SCORE={found[-1]} is outside 0..1"
    return RunResult(case, arm, run, None, error, log)


def run_manual(ws: Path, cases: list[str], args: argparse.Namespace,
               inputs: dict[str, str]) -> dict[str, tuple[str, list[float]]]:
    """Run every case's arms and write uplift.json. A without-skill arm cached under the case's
    baseline_key (same case, model, executor, run-case.sh, suite and gate inputs) is reused unless
    --fresh-baseline, and the artifact records the entry's key and hash. Returns the complete fresh
    without-skill arms by case, (key, scores), for run_gate to cache once the inputs are unchanged."""
    keys = {case: baseline_key(ws, case, args.model, args.executor, inputs) for case in cases}
    cached = {} if args.fresh_baseline else {
        case: scores for case in cases if (scores := read_baseline(ws, keys[case], args.runs)) is not None}
    if cached:
        print(f"gate: reusing the cached without-skill arm for {len(cached)} of {len(cases)} cases "
              f"({BASELINE_CACHE.as_posix()}/; --fresh-baseline re-runs them)", file=sys.stderr)
    jobs = [(i, case, arm, run) for i, case in enumerate(cases)
            for arm in ("with", "without") for run in range(1, args.runs + 1)
            if not (arm == "without" and case in cached)]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(_one_run, ws, *job, args) for job in jobs]
        pending = set(futures)
        while pending and not _STOP.is_set():
            pending = futures_wait(pending, timeout=POLL_SECONDS).not_done
    if _STOP.is_set():
        raise GateStopped()
    results = [f.result() for f in futures]

    out_cases, errors, fresh = [], [], {}
    for case in cases:
        arms, entry = {}, {"case": case}
        for arm in ("with", "without"):
            mine = sorted((r for r in results if r.case == case and r.arm == arm), key=lambda r: r.run)
            arms[arm] = [r.score for r in mine if r.score is not None]
            errors += [{"case": case, "arm": arm, "run": r.run, "error": r.error, "log": r.log}
                       for r in mine if r.error]
        if case in cached:
            arms["without"] = cached[case]
            entry["without_cache"] = {"key": keys[case], "sha256": file_sha256(baseline_path(ws, keys[case]))}
        elif len(arms["without"]) == args.runs:
            fresh[case] = (keys[case], arms["without"])
        delta = (round(sum(arms["with"]) / len(arms["with"]) - sum(arms["without"]) / len(arms["without"]), 4)
                 if arms["with"] and arms["without"] else None)
        out_cases.append({**entry, "with": arms["with"], "without": arms["without"], "delta": delta})

    def mean_of(arm: str) -> float | None:
        scores = [s for c in out_cases for s in c[arm]]
        return round(sum(scores) / len(scores), 4) if scores else None

    with_mean, without_mean = mean_of("with"), mean_of("without")
    write_json(ws / MANUAL_ARTIFACT, {
        "cases": out_cases,
        "summary": {
            "model": args.model, "executor": args.executor, "runs_per_case": args.runs,
            "in_sample": args.in_sample, "mean_with": with_mean, "mean_without": without_mean,
            "mean_delta": (round(with_mean - without_mean, 4)
                           if with_mean is not None and without_mean is not None else None),
        },
        "partial": bool(errors),
        "errors": errors,
    })
    for err in errors:
        print(f"gate: {err['case']} {err['arm']} run {err['run']}: {err['error']} (log {err['log']})", file=sys.stderr)
    return fresh


def cache_baselines(ws: Path, fresh: dict[str, tuple[str, list[float]]], args: argparse.Namespace) -> None:
    for case, (key, scores) in fresh.items():
        path = baseline_path(ws, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {"key": key, "case": case, "model": args.model, "executor": args.executor,
                          "runs": scores, "written": _now()})


def _watch_stop_file() -> None:
    """run_owned's stop file asks the job to stop; on Windows it is the only request before the Job
    Object ends, so the gate stops its eval runs itself while the grace period lasts."""
    stop_file = os.environ.get("RUN_OWNED_STOP_FILE")
    if not stop_file:
        return

    def watch() -> None:
        while not _STOP.wait(POLL_SECONDS):
            if os.path.exists(stop_file):
                _STOP.set()

    threading.Thread(target=watch, daemon=True).start()


def run_gate(ws: Path, args: argparse.Namespace) -> int:
    """The gate itself; runs inside the owned `gate` job."""
    signal.signal(signal.SIGTERM, lambda *_: _STOP.set())
    _watch_stop_file()
    try:
        skill, cases, mode, available, invoked = preconditions(ws, args)
        artifact = PLUGIN_ARTIFACT if mode == "plugin" else MANUAL_ARTIFACT
        record = {"mode": mode, "backend": args.backend or default_backend(), "plugin_eval_available": available,
                  "model_invoked": invoked, "force_manual": args.force_manual,
                  "fresh_baseline": args.fresh_baseline, "skill": skill,
                  "model": args.model, "runs": args.runs, "in_sample": args.in_sample, "executor": args.executor,
                  "cases": cases, "artifact": artifact.as_posix(), **_input_hashes(ws, skill, cases),
                  "started": _now(), "finished": None}
        write_json(ws / GATE_RECORD, record)
        _set_aside(ws / artifact)
        print(f"gate: mode={mode} (plugin eval probe exit {available}, model-invoked={invoked}), "
              f"{len(cases)} cases x {args.runs} runs per arm", file=sys.stderr)
        log_attempt(ws, "gate")
        fresh = {}
        if mode == "plugin":
            run_plugin(ws, skill, args)
        else:
            fresh = run_manual(ws, cases, args, record["inputs_sha256"])
    except GateError as exc:
        log_attempt(ws, "gate", str(exc))
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    except GateStopped:
        _stop_children(run_owned.state_dir(ws))
        print("gate: stopped before it finished; gate.json records no finished run", file=sys.stderr)
        return EXIT_STOPPED

    now = _input_hashes(ws, skill, cases)
    changed = sorted(key for key in now if now[key] != record[key])
    if changed:
        record["changed_during_run"] = changed
        write_json(ws / GATE_RECORD, record)
        print(f"gate: GATE INVALID: gate inputs changed while the gate ran ({', '.join(changed)}); gate.json records "
              "no finished run; re-run 5.0 without editing the workspace meanwhile", file=sys.stderr)
        return EXIT_INVALID
    cache_baselines(ws, fresh, args)
    record |= {"artifact_sha256": file_sha256(ws / artifact), "finished": _now()}
    write_json(ws / GATE_RECORD, record)
    verdict = check_gate.validate(ws, skill)
    print(verdict.line())
    return 0 if verdict.valid else EXIT_INVALID


def _input_hashes(ws: Path, skill: str, cases: list[str]) -> dict:
    """What the gate binds its run to, taken at the start and compared again at the end."""
    return {"skill_sha256": skill_sha256(ws, skill), "cases_sha256": cases_sha256(ws, cases),
            "split_sha256": split_sha256(read_data_split(ws / DATA_SPLIT)),
            "run_case_sha256": file_sha256(ws / RUN_CASE), "inputs_sha256": inputs_sha256(ws)}


def _inside_gate_job(ws: Path) -> bool:
    """True when this process is the `gate` job's command: its token matches the job's record."""
    token = os.environ.get("RUN_OWNED_TOKEN")
    if os.environ.get("RUN_OWNED_JOB") != JOB or not token:
        return False
    try:
        return run_owned.load(run_owned.state_dir(ws), JOB).token == token
    except run_owned.OwnedError:
        return False


def _running(sdir: Path) -> dict | None:
    try:
        status = run_owned.status(sdir, JOB)
    except run_owned.OwnedError:
        return None
    return status if status["state"] == "running" else None


def _job_argv(ws: Path, args: argparse.Namespace) -> list[str]:
    argv = [sys.executable, "-B", str(SCRIPTS / "gate.py"), "--workspace", str(ws), "--model", args.model,
            "--runs", str(args.runs), "--jobs", str(args.jobs), "--allow-tools", *args.allow_tools,
            "--timeout", f"{args.timeout:g}", "--case-timeout", f"{args.case_timeout:g}"]
    if args.skill is not None:
        argv += ["--skill", args.skill]
    if args.backend:
        argv += ["--backend", args.backend]
    if args.force_manual:
        argv += ["--force-manual", args.force_manual]
    if args.in_sample:
        argv.append("--in-sample")
    if args.fresh_baseline:
        argv.append("--fresh-baseline")
    if args.executor:
        argv += ["--executor", args.executor]
    return argv


PYTHON = f'"{sys.executable}"' if os.name == "nt" else "python3"


def _wait_hint(ws: Path) -> str:
    return f"{PYTHON} {SCRIPTS / 'gate.py'} --workspace {ws} --wait"


def _elapsed(started: str) -> str:
    try:
        seconds = int((datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())
    except (TypeError, ValueError):
        return "an unknown time"
    return f"{seconds // 3600}h{seconds // 60 % 60:02d}m" if seconds >= 3600 else f"{seconds // 60}m{seconds % 60:02d}s"


def _last_line(log: str) -> str:
    try:
        lines = [line for line in Path(log).read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[-1].strip() if lines else ""


def wait_gate(ws: Path, timeout: float, proc: subprocess.Popen | None = None) -> int:
    """Block on the recorded `gate` job for up to `timeout` seconds. Finished: print its log, return its exit
    code. Still running: print one progress line, return 124. No job recorded: 3."""
    sdir = run_owned.state_dir(ws)
    try:
        rec = run_owned.load(sdir, JOB)
    except run_owned.OwnedError:
        print(f"gate: no `{JOB}` job is recorded; start one with {PYTHON} {SCRIPTS / 'gate.py'} --detach "
              "--model <model> [options]", file=sys.stderr)
        return EXIT_PRECONDITION
    rc = run_owned.wait(sdir, JOB, timeout=timeout, proc=proc)
    if rc == run_owned.EXIT_TIMEOUT:
        last = _last_line(rec.log)
        print(f"gate: still running after {_elapsed(rec.started)}{f' (last: {last})' if last else ''}; call --wait "
              f"again (exit 124), not run_owned.py status: {_wait_hint(ws)}", file=sys.stderr)
        return rc
    sys.stdout.write(Path(rec.log).read_text(encoding="utf-8", errors="replace"))
    if rc == run_owned.EXIT_NO_STATUS:
        print(f"gate: the `{JOB}` job ended without recording an exit status; see {rec.log}", file=sys.stderr)
    return rc


def _stop(sdir: Path) -> int:
    if _running(sdir) is None:
        print(f"gate: no running `{JOB}` job", file=sys.stderr)
        return 0
    print(f"gate: {run_owned.stop(sdir, JOB, grace=STOP_GRACE)}", file=sys.stderr)
    return 0


def supervise(ws: Path, args: argparse.Namespace) -> int:
    """Start the gate as the owned job `gate`, then wait for it unless --detach."""
    sdir = run_owned.state_dir(ws)
    live = _running(sdir)
    if live and not args.restart:
        print(f"gate: a `{JOB}` job is already running (pgid {live['pgid']}, log {live['log']}).\n"
              f"  wait:      {_wait_hint(ws)}\n"
              f"  restart:   {PYTHON} {SCRIPTS / 'gate.py'} --restart <the same options>", file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        if args.detach or args.restart:
            preconditions(ws, args)
    except GateError as exc:
        log_attempt(ws, "gate", str(exc))
        print(f"gate: {exc}", file=sys.stderr)
        print(f"gate: not started{'; the running gate was left as it is' if live else ''}", file=sys.stderr)
        return EXIT_PRECONDITION
    if live:
        print(f"gate: {run_owned.stop(sdir, JOB, grace=STOP_GRACE)}", file=sys.stderr)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = run_owned.start(sdir, JOB, _job_argv(ws, args), cwd=ws, env=env)
    rec = run_owned.load(sdir, JOB)
    log = rec.log
    print(f"gate: started as owned job `{JOB}` (pgid {rec.pgid}); log {log}", file=sys.stderr)
    if args.wait:
        return wait_gate(ws, args.wait_timeout, proc)
    if args.detach or args.restart:
        print(f"gate: block until it exits (its exit code is the gate's; 124: call it again):\n  {_wait_hint(ws)}",
              file=sys.stderr)
        return 0
    print(f"gate: waiting; if this call is cut off the gate keeps running: {_wait_hint(ws)}", file=sys.stderr)
    try:
        rc = run_owned.wait(sdir, JOB, proc=proc)
    except KeyboardInterrupt:
        print(f"\ngate: still running in the background: {_wait_hint(ws)}", file=sys.stderr)
        return 130
    sys.stdout.write(Path(log).read_text(encoding="utf-8", errors="replace"))
    return rc


def record(ws: Path, skill: str | None, backend: str | None, force_manual: str | None) -> int:
    try:
        cases, mode, available, _ = record_cases(ws, resolve_skill(ws, skill), backend, force_manual)
    except GateError as exc:
        log_attempt(ws, "record", str(exc))
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    log_attempt(ws, "record")
    print(f"gate: recorded {len(cases)} gate cases for mode {mode} (plugin eval probe exit {available}) in "
          f"{DATA_SPLIT}: {', '.join(cases)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=["record"],
                        help="record: write the case list into data-split.yaml from disk, then exit")
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the only one)")
    parser.add_argument("--backend", choices=["plugin", "manual"],
                        help="plugin: claude plugin eval (Claude Code); manual: run-case.sh (default: plugin "
                             "inside Claude Code when it can run, else manual)")
    parser.add_argument("--force-manual", metavar="'<host>: <reason>'",
                        help="with --backend manual: run it inside Claude Code on a model-invoked skill plugin eval "
                             "could run (another host whose shell inherits $CLAUDECODE); logged in gate.json and "
                             "data-split.yaml")
    parser.add_argument("--model", help="the model the skill targets (pinned for both arms; required unless --stop)")
    parser.add_argument("--runs", type=int, default=REQUIRED_RUNS,
                        help=f"runs per arm per case (default {REQUIRED_RUNS}; check_gate requires {REQUIRED_RUNS})")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS, help=f"concurrent runs, 1-8 (default {DEFAULT_JOBS})")
    parser.add_argument("--allow-tools", nargs="+", default=["Bash", "Write"],
                        help="plugin mode: gated tools the skill needs (default: Bash Write)")
    parser.add_argument("--in-sample", action="store_true",
                        help="the gate cases are also training cases (3-9 gold standards)")
    parser.add_argument("--executor", choices=["single-call", "agentic"],
                        help="manual mode: how run-case.sh executes the skill (required there)")
    parser.add_argument("--fresh-baseline", action="store_true",
                        help="manual mode: re-run every without-skill arm instead of reusing work/evaluation/"
                             "baseline-cache/")
    parser.add_argument("--timeout", type=float, default=EVAL_TIMEOUT,
                        help=f"plugin mode: seconds for the whole eval (default {EVAL_TIMEOUT})")
    parser.add_argument("--case-timeout", type=float, default=CASE_TIMEOUT,
                        help=f"manual mode: seconds per run (default {CASE_TIMEOUT})")
    parser.add_argument("--detach", action="store_true", help="start the `gate` job and return; then block with --wait")
    parser.add_argument("--restart", action="store_true",
                        help="stop the recorded `gate` job, then start a new one detached")
    parser.add_argument("--stop", action="store_true", help="stop the recorded `gate` job and its eval runs")
    parser.add_argument("--wait", action="store_true",
                        help="block on the recorded `gate` job (after starting it, with --detach or --restart); "
                             "exit 124 while it still runs: call --wait again")
    parser.add_argument("--wait-timeout", type=float, default=WAIT_SECONDS,
                        help=f"--wait: seconds to block before exiting 124 (default {WAIT_SECONDS}, under a "
                             "600 s shell-call limit)")
    args = parser.parse_args(argv)
    if args.force_manual is not None:
        host, _, reason = " ".join(args.force_manual.split()).partition(":")
        if args.backend != "manual" or not host.strip() or not reason.strip():
            parser.error("--force-manual goes with --backend manual and names the host and why: "
                         "--force-manual '<host>: <reason>'")
        if re.search(r"claude[\s_-]*code", host, re.IGNORECASE):
            parser.error("--force-manual names another host, never Claude Code: a Claude Code build runs the plugin "
                         "gate; drop --backend manual")
        args.force_manual = f"{host.strip()}: {reason.strip()}"
    if args.command == "record":
        if args.stop or args.detach or args.restart or args.wait:
            parser.error("record takes no --stop, --detach, --restart or --wait")
        return record(args.workspace.resolve(), args.skill, args.backend, args.force_manual)
    if args.stop and (args.detach or args.restart or args.wait):
        parser.error("--stop takes no --detach, --restart or --wait")
    if args.wait_timeout <= 0:
        parser.error("--wait-timeout must be > 0")
    bare_wait = args.wait and not (args.detach or args.restart)
    if not (args.stop or bare_wait) and not args.model:
        parser.error("--model is required (except with --stop or a bare --wait)")
    if args.runs < 1 or not 1 <= args.jobs <= 8:
        parser.error("--runs must be >= 1 and --jobs 1-8")

    ws = args.workspace.resolve()
    try:
        if args.stop:
            return _stop(run_owned.state_dir(ws))
        if bare_wait:
            return wait_gate(ws, args.wait_timeout)
        if _inside_gate_job(ws):
            return run_gate(ws, args)
        return supervise(ws, args)
    except run_owned.OwnedError as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION


if __name__ == "__main__":
    sys.exit(main())
