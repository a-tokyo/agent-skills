#!/usr/bin/env python3
"""Run the Phase 5.0 eval gate end to end and write its canonical artifact.

The gate runs as the owned job `gate` (run_owned.py; record and log in work/pids/), whether you
start it with this script or with `run_owned.py start gate -- python3 gate.py ...`. A full gate can
take over an hour, longer than an agent's shell call waits, so start it detached and poll:

    gate.py --detach --model <model> [options]          # starts the job, prints how to poll
    run_owned.py wait gate --timeout 540                # repeat until it exits; its exit code is the gate's
    gate.py --restart --model <model> [options]         # stops only the recorded `gate` group, starts it detached
    gate.py --stop                                      # stops it (and the eval runs it started)

Without --detach it starts the job and waits for it (for humans at a terminal). If that call is cut
off, the job keeps running. A second start while a `gate` job is live is refused. --detach and
--restart first check the preconditions synchronously (the suite, the recorded cases, each case's
`scaffold_script`, run-case.sh and --executor in manual mode) and exit 3 without starting anything,
or stopping a running gate, when one is missing.

Mode: `plugin` when `claude plugin eval --help` exits 0 and the skill is model-invoked, otherwise
`manual`. The case list is never typed by hand: `gate.py record` (3.6) writes `plugin_eval` and
`plugin_eval_available` into work/evaluation/data-split.yaml from disk, and every gate run records
again before it starts, so the list always matches what runs:

  plugin  every case directory under work/evaluation/plugin/evals/, after a zero-cost load check
          (`claude plugin eval . --max-cost-usd 0`) that fails on any case file plugin eval cannot
          load — it would skip that case and the artifact would miss it
  manual  the gate gold standards from the split: `test` when it lists any, else every entry of
          `all_cases`, `training` and `validation`; run-case.sh gets each entry as its <case>

    gate.py record [--workspace DIR] [--skill NAME]     # exit 0 recorded, 3 precondition missing

Each eval run goes in its own owned process group, stopped on timeout.

  plugin  refresh work/evaluation/plugin/skills/<skill>/ from output/<skill>/, then run
          `claude plugin eval .` with and without the skill -> work/evaluation/plugin-eval.json
  manual  call work/evaluation/run-case.sh <case> <with|without> --runs times per arm per case;
          it prints SCORE=<0..1> as its last SCORE line -> work/evaluation/uplift.json
          (a failed run is retried once; a second failure leaves the artifact partial)

It records the mode, the probe's exit code and sha256 hashes of output/<skill>/, of the case set and,
when the run finishes, of run-case.sh, the scorer and fixture files (gate_common.inputs_sha256) and
the artifact in work/evaluation/gate.json (check_gate.py fails once any changes), moves a previous artifact
aside to <name>.prev.json before running so a stale file can never pass, ends by running
check_gate.py, and writes nothing outside work/evaluation/ and work/pids/.

    gate.py --model <model> [--workspace DIR] [--skill NAME] [--runs 3] [--jobs 4]
            [--allow-tools Bash Write] [--in-sample] [--executor single-call|agentic]
            [--timeout 5400] [--case-timeout 1200] [--detach] [--restart]
    gate.py --stop [--workspace DIR]

run-case.sh receives GATE_MODEL and GATE_RUN in its environment and runs from the workspace root; on
Windows the gate runs it with Git for Windows' bash (CLAUDE_CODE_GIT_BASH_PATH, else bash on PATH or the
default Git install; WSL's bash.exe is skipped). Tested on macOS, Linux and Windows.

Exit codes: 0 complete and valid (--detach, --restart, --stop: started or stopped); 1 the gate ran but the artifact is
missing, partial or invalid (the message says why); 2 usage error; 3 a precondition is missing
(suite, cases, run-case.sh) or a `gate` job is already running; 143 the gate was stopped.
"""

from __future__ import annotations

import argparse
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
    DATA_SPLIT, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, PLUGIN_DIR, REQUIRED_RUNS, RUN_CASE,
    GateError, cases_sha256, file_sha256, inputs_sha256, model_invoked, omitted_gold, read_data_split, resolve_skill,
    log_attempt, skill_sha256, utf8_stdio, write_gate_cases, write_json,
)

EXIT_INVALID = 1
EXIT_PRECONDITION = 3
EXIT_STOPPED = 143
JOB = "gate"
PROBE_TIMEOUT = 60
POLL_SECONDS = 0.5
STOP_GRACE = 30
CHILD_GRACE = 10
WAIT_HINT_SECONDS = 540
SCORE_RE = re.compile(r"^SCORE=(\S+)\s*$", re.MULTILINE)
SCAFFOLD_RE = re.compile(r"^\s*scaffold_script:\s*(\S.*)$")
NAME_RE = re.compile(r"^name:\s*(\S.*)$")
LOAD_CHECK_TIMEOUT = 120
NOT_PROBED = "not-probed"
ATTEMPTS = 2
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


def record_cases(ws: Path, skill: str) -> tuple[list[str], str, int | str, bool]:
    """Write the gate's case list into data-split.yaml from what is on disk: (cases, mode, probe exit
    code, model-invoked). Plugin mode lists the suite's case directories after a load check; manual mode
    lists the gate gold standards from the split (3.6). A user-invoked skill is always manual, so the
    probe is skipped and nothing calls `claude`; manual mode never calls it either."""
    invoked = model_invoked(ws / "output" / skill / "SKILL.md")
    available: int | str = probe_plugin_eval() if invoked else NOT_PROBED
    mode = "plugin" if available == 0 else "manual"
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
        omitted = omitted_gold(ws, [c for key in ("all_cases", "training", "validation", "test")
                                    for c in split.gold.get(key, [])])
        if omitted:
            raise GateError(f"{DATA_SPLIT} leaves out gold standards found on disk: {', '.join(omitted)}; list each "
                            "under all_cases, training, validation or test (4.2), or move a file that is not a "
                            "gold standard out of that folder")
    write_gate_cases(ws / DATA_SPLIT, cases, available, source)
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
            run_owned.stop(sdir, name, grace=10, proc=proc)
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
    cases, mode, available, invoked = record_cases(ws, skill)
    check_scaffolds(ws, cases)
    if mode == "manual":
        check_adapter(ws, args)
    return skill, cases, mode, available, invoked


def run_plugin(ws: Path, skill: str, args: argparse.Namespace) -> None:
    plugin = ws / PLUGIN_DIR
    copy = plugin / "skills" / skill
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
    env = {**os.environ, "GATE_MODEL": args.model, "GATE_RUN": str(run)}
    error, log = "not run", ""
    for attempt in range(1, ATTEMPTS + 1):
        name = f"gate-c{idx:02d}-{arm}-r{run}-a{attempt}"
        rc, log = run_owned_job(sdir, name, [*_adapter_argv(ws), case, arm], ws, args.case_timeout, env)
        if rc is None:
            error = f"timed out after {args.case_timeout:g}s"
            continue
        found = SCORE_RE.findall(Path(log).read_text(encoding="utf-8", errors="replace"))
        if rc != 0:
            error = f"exit {rc}"
        elif not found:
            error = "no SCORE= line"
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


def run_manual(ws: Path, cases: list[str], args: argparse.Namespace) -> None:
    jobs = [(i, case, arm, run) for i, case in enumerate(cases)
            for arm in ("with", "without") for run in range(1, args.runs + 1)]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(_one_run, ws, *job, args) for job in jobs]
        pending = set(futures)
        while pending and not _STOP.is_set():
            pending = futures_wait(pending, timeout=POLL_SECONDS).not_done
    if _STOP.is_set():
        raise GateStopped()
    results = [f.result() for f in futures]

    out_cases, errors = [], []
    for case in cases:
        arms = {}
        for arm in ("with", "without"):
            mine = sorted((r for r in results if r.case == case and r.arm == arm), key=lambda r: r.run)
            arms[arm] = [r.score for r in mine if r.score is not None]
            errors += [{"case": case, "arm": arm, "run": r.run, "error": r.error, "log": r.log}
                       for r in mine if r.error]
        delta = (round(sum(arms["with"]) / len(arms["with"]) - sum(arms["without"]) / len(arms["without"]), 4)
                 if arms["with"] and arms["without"] else None)
        out_cases.append({"case": case, "with": arms["with"], "without": arms["without"], "delta": delta})

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
        record = {"mode": mode, "plugin_eval_available": available, "model_invoked": invoked, "skill": skill,
                  "model": args.model, "runs": args.runs, "in_sample": args.in_sample,
                  "cases": cases, "artifact": artifact.as_posix(),
                  "skill_sha256": skill_sha256(ws, skill), "cases_sha256": cases_sha256(ws, cases),
                  "started": _now(), "finished": None}
        write_json(ws / GATE_RECORD, record)
        _set_aside(ws / artifact)
        print(f"gate: mode={mode} (plugin eval probe exit {available}, model-invoked={invoked}), "
              f"{len(cases)} cases x {args.runs} runs per arm", file=sys.stderr)
        log_attempt(ws, "gate")
        if mode == "plugin":
            run_plugin(ws, skill, args)
        else:
            run_manual(ws, cases, args)
    except GateError as exc:
        log_attempt(ws, "gate", str(exc))
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    except GateStopped:
        _stop_children(run_owned.state_dir(ws))
        print("gate: stopped before it finished; gate.json records no finished run", file=sys.stderr)
        return EXIT_STOPPED

    record |= {"run_case_sha256": file_sha256(ws / RUN_CASE), "inputs_sha256": inputs_sha256(ws),
               "artifact_sha256": file_sha256(ws / artifact), "finished": _now()}
    write_json(ws / GATE_RECORD, record)
    verdict = check_gate.validate(ws, skill, REQUIRED_RUNS)
    print(verdict.line())
    return 0 if verdict.valid else EXIT_INVALID


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
    if args.skill:
        argv += ["--skill", args.skill]
    if args.in_sample:
        argv.append("--in-sample")
    if args.executor:
        argv += ["--executor", args.executor]
    return argv


PYTHON = f'"{sys.executable}"' if os.name == "nt" else "python3"


def _wait_hint(ws: Path) -> str:
    return f"{PYTHON} {SCRIPTS / 'run_owned.py'} --workspace {ws} wait {JOB} --timeout {WAIT_HINT_SECONDS}"


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
              f"  poll it:   {_wait_hint(ws)}\n"
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
    if args.detach or args.restart:
        print(f"gate: poll until it exits (its exit code is the gate's):\n  {_wait_hint(ws)}", file=sys.stderr)
        return 0
    print(f"gate: waiting; if this call is cut off the gate keeps running: {_wait_hint(ws)}", file=sys.stderr)
    try:
        rc = run_owned.wait(sdir, JOB, proc=proc)
    except KeyboardInterrupt:
        print(f"\ngate: still running in the background: {_wait_hint(ws)}", file=sys.stderr)
        return 130
    sys.stdout.write(Path(log).read_text(encoding="utf-8", errors="replace"))
    return rc


def record(ws: Path, skill: str | None) -> int:
    try:
        cases, mode, available, _ = record_cases(ws, resolve_skill(ws, skill))
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
    parser.add_argument("--model", help="the model the skill targets (pinned for both arms; required unless --stop)")
    parser.add_argument("--runs", type=int, default=REQUIRED_RUNS,
                        help=f"runs per arm per case (default {REQUIRED_RUNS}; check_gate requires {REQUIRED_RUNS})")
    parser.add_argument("--jobs", type=int, default=4, help="concurrent runs, 1-8 (default 4)")
    parser.add_argument("--allow-tools", nargs="+", default=["Bash", "Write"],
                        help="plugin mode: gated tools the skill needs (default: Bash Write)")
    parser.add_argument("--in-sample", action="store_true",
                        help="the gate cases are also training cases (3-9 gold standards)")
    parser.add_argument("--executor", choices=["single-call", "agentic"],
                        help="manual mode: how run-case.sh executes the skill (required there)")
    parser.add_argument("--timeout", type=float, default=5400, help="plugin mode: seconds for the whole eval")
    parser.add_argument("--case-timeout", type=float, default=1200, help="manual mode: seconds per run")
    parser.add_argument("--detach", action="store_true", help="start the `gate` job and return; poll with run_owned.py wait")
    parser.add_argument("--restart", action="store_true",
                        help="stop the recorded `gate` job, then start a new one detached")
    parser.add_argument("--stop", action="store_true", help="stop the recorded `gate` job and its eval runs")
    args = parser.parse_args(argv)
    if args.command == "record":
        if args.stop or args.detach or args.restart:
            parser.error("record takes no --stop, --detach or --restart")
        return record(args.workspace.resolve(), args.skill)
    if args.stop and (args.detach or args.restart):
        parser.error("--stop takes no --detach or --restart")
    if not args.stop and not args.model:
        parser.error("--model is required (except with --stop)")
    if args.runs < 1 or not 1 <= args.jobs <= 8:
        parser.error("--runs must be >= 1 and --jobs 1-8")

    ws = args.workspace.resolve()
    try:
        if args.stop:
            return _stop(run_owned.state_dir(ws))
        if _inside_gate_job(ws):
            return run_gate(ws, args)
        return supervise(ws, args)
    except run_owned.OwnedError as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION


if __name__ == "__main__":
    sys.exit(main())
