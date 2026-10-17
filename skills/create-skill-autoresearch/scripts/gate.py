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
off, the job keeps running. A second start while a `gate` job is live is refused.

Mode: `plugin` when `claude plugin eval --help` exits 0 and the skill is model-invoked, otherwise
`manual`. Both run the gate cases recorded under `plugin_eval` in work/evaluation/data-split.yaml,
each eval run in its own owned process group, stopped on timeout.

  plugin  refresh work/evaluation/plugin/skills/<skill>/ from output/<skill>/, then run
          `claude plugin eval .` with and without the skill -> work/evaluation/plugin-eval.json
  manual  call work/evaluation/run-case.sh <case> <with|without> --runs times per arm per case;
          it prints SCORE=<0..1> as its last SCORE line -> work/evaluation/uplift.json
          (a failed run is retried once; a second failure leaves the artifact partial)

It records the mode, the probe's exit code and sha256 hashes of output/<skill>/ and of the case set
in work/evaluation/gate.json (check_gate.py fails once either changes), moves a previous artifact
aside to <name>.prev.json before running so a stale file can never pass, ends by running
check_gate.py, and writes nothing outside work/evaluation/ and work/pids/.

    gate.py --model <model> [--workspace DIR] [--skill NAME] [--runs 3] [--jobs 4]
            [--allow-tools Bash Write] [--in-sample] [--executor single-call|agentic]
            [--timeout 5400] [--case-timeout 1200] [--detach] [--restart]
    gate.py --stop [--workspace DIR]

run-case.sh receives GATE_MODEL and GATE_RUN in its environment and runs from the workspace root.
Tested on macOS and Linux.

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
    GateError, cases_sha256, model_invoked, read_data_split, resolve_skill, skill_sha256, write_json,
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


def probe_plugin_eval() -> int:
    """Exit code of `claude plugin eval --help`: 127 if claude is missing, 124 on timeout."""
    try:
        return subprocess.run(["claude", "plugin", "eval", "--help"], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=PROBE_TIMEOUT).returncode
    except FileNotFoundError:
        return 127
    except subprocess.TimeoutExpired:
        return 124


def _set_aside(path: Path) -> None:
    if path.exists():
        os.replace(path, path.with_name(path.stem + ".prev.json"))


def _eval_case_dirs(evals: Path) -> list[str]:
    return sorted(p.name for p in evals.iterdir()
                  if p.is_dir() and ((p / "case.yaml").is_file() or (p / "prompt.md").is_file()))


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


def run_plugin(ws: Path, skill: str, cases: list[str], args: argparse.Namespace) -> None:
    plugin = ws / PLUGIN_DIR
    evals = plugin / "evals"
    if not (plugin / ".claude-plugin" / "plugin.json").is_file() or not evals.is_dir():
        raise GateError(f"{PLUGIN_DIR} needs .claude-plugin/plugin.json and evals/ (scaffold the suite in 3.6)")
    on_disk = _eval_case_dirs(evals)
    missing = [c for c in cases if c not in on_disk]
    extra = [c for c in on_disk if c not in cases]
    if missing or extra:
        raise GateError(f"evals/ and data-split.yaml plugin_eval disagree: recorded but absent {missing or 'none'}, "
                        f"present but unrecorded {extra or 'none'}")
    copy = plugin / "skills" / skill
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(ws / "output" / skill, copy)

    cmd = ["claude", "plugin", "eval", ".", "--ablation", "with-without", "--runs", str(args.runs),
           "-j", str(args.jobs), "--model", args.model, "--allow-tools", *args.allow_tools,
           "--scaffold", "--trust-plugin", "--no-publish", "--threshold", "0",
           "--json", str((ws / PLUGIN_ARTIFACT).resolve())]
    rc, log = run_owned_job(run_owned.state_dir(ws), "gate-plugin-eval", cmd, plugin, args.timeout)
    if rc is None:
        print(f"gate: claude plugin eval timed out after {args.timeout:g}s and was stopped; log: {log}", file=sys.stderr)
    elif rc != 0:
        print(f"gate: claude plugin eval exited {rc}; log: {log}", file=sys.stderr)


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
        rc, log = run_owned_job(sdir, name, [str((ws / RUN_CASE).resolve()), case, arm], ws, args.case_timeout, env)
        if rc is None:
            error = f"timed out after {args.case_timeout:g}s"
            continue
        found = SCORE_RE.findall(Path(log).read_text(errors="replace"))
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
    adapter = ws / RUN_CASE
    if not adapter.is_file() or not os.access(adapter, os.X_OK):
        raise GateError(f"{RUN_CASE} is missing or not executable; 3.6 writes it (chmod +x)")
    if args.executor is None:
        raise GateError("manual mode: pass --executor single-call or agentic (recorded in uplift.json)")
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


def run_gate(ws: Path, args: argparse.Namespace) -> int:
    """The gate itself; runs inside the owned `gate` job."""
    signal.signal(signal.SIGTERM, lambda *_: _STOP.set())
    try:
        skill = resolve_skill(ws, args.skill)
        split = read_data_split(ws / DATA_SPLIT)
        if not split.plugin_eval:
            raise GateError(f"{DATA_SPLIT}: {split.plugin_eval_error or 'plugin_eval records no gate cases'}")
        available = probe_plugin_eval()
        invoked = model_invoked(ws / "output" / skill / "SKILL.md")
        mode = "plugin" if available == 0 and invoked else "manual"
        artifact = PLUGIN_ARTIFACT if mode == "plugin" else MANUAL_ARTIFACT
        record = {"mode": mode, "plugin_eval_available": available, "model_invoked": invoked, "skill": skill,
                  "model": args.model, "runs": args.runs, "in_sample": args.in_sample,
                  "cases": split.plugin_eval, "artifact": str(artifact),
                  "skill_sha256": skill_sha256(ws, skill), "cases_sha256": cases_sha256(ws, split.plugin_eval),
                  "started": _now(), "finished": None}
        write_json(ws / GATE_RECORD, record)
        _set_aside(ws / artifact)
        print(f"gate: mode={mode} (plugin eval probe exit {available}, model-invoked={invoked}), "
              f"{len(split.plugin_eval)} cases x {args.runs} runs per arm", file=sys.stderr)
        if mode == "plugin":
            run_plugin(ws, skill, split.plugin_eval, args)
        else:
            run_manual(ws, split.plugin_eval, args)
    except GateError as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    except GateStopped:
        _stop_children(run_owned.state_dir(ws))
        print("gate: stopped before it finished; gate.json records no finished run", file=sys.stderr)
        return EXIT_STOPPED

    record["finished"] = _now()
    write_json(ws / GATE_RECORD, record)
    verdict = check_gate.validate(ws, skill, REQUIRED_RUNS)
    print(verdict.line())
    return 0 if verdict.valid else EXIT_INVALID


def _inside_gate_job() -> bool:
    return os.environ.get("RUN_OWNED_JOB") == JOB and os.environ.get("RUN_OWNED_PGID") == str(os.getpgrp())


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


def _wait_hint(ws: Path) -> str:
    return f"python3 {SCRIPTS / 'run_owned.py'} --workspace {ws} wait {JOB} --timeout {WAIT_HINT_SECONDS}"


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
    if live and args.restart:
        print(f"gate: {run_owned.stop(sdir, JOB, grace=STOP_GRACE)}", file=sys.stderr)
    elif live:
        print(f"gate: a `{JOB}` job is already running (pgid {live['pgid']}, log {live['log']}).\n"
              f"  poll it:   {_wait_hint(ws)}\n"
              f"  restart:   python3 {SCRIPTS / 'gate.py'} --restart <the same options>", file=sys.stderr)
        return EXIT_PRECONDITION
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = run_owned.start(sdir, JOB, _job_argv(ws, args), cwd=ws, env=env)
    log = run_owned.load(sdir, JOB).log
    print(f"gate: started as owned job `{JOB}` (pgid {proc.pid}); log {log}", file=sys.stderr)
    if args.detach or args.restart:
        print(f"gate: poll until it exits (its exit code is the gate's):\n  {_wait_hint(ws)}", file=sys.stderr)
        return 0
    print(f"gate: waiting; if this call is cut off the gate keeps running: {_wait_hint(ws)}", file=sys.stderr)
    try:
        rc = run_owned.wait(sdir, JOB, proc=proc)
    except KeyboardInterrupt:
        print(f"\ngate: still running in the background: {_wait_hint(ws)}", file=sys.stderr)
        return 130
    sys.stdout.write(Path(log).read_text(errors="replace"))
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        if _inside_gate_job():
            return run_gate(ws, args)
        return supervise(ws, args)
    except run_owned.OwnedError as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION


if __name__ == "__main__":
    sys.exit(main())
