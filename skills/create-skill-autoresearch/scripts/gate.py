#!/usr/bin/env python3
"""Run the Phase 5.0 eval gate as the owned job `gate` and write its canonical artifact.

    gate.py record [--skill NAME] [--backend plugin|manual [--force-manual '<host>: <reason>']]
    gate.py --detach --model <model> [options]    # check preconditions, start the job, return; then:
    gate.py --wait [--wait-timeout 540]           # block; exit 124 = still running: call --wait again
    gate.py --restart --model <model> [options]   # stop only the recorded `gate` group, start it detached
    gate.py --stop                                # stop it and the eval runs it started

Without --detach, --restart, --wait or --stop it starts the job and waits (for a terminal). What it runs, records
and hashes: references/phase-5.md "Gate Runner"; plugin or manual mode: phase-3.md 3.6; the manual protocol
and its baseline cache: benchmark-standard.md. `record` writes plugin_eval into data-split.yaml from disk, and
every gate run records again first. It writes only under work/evaluation/ and work/pids/.

Exit codes: 0 complete and valid (--detach, --restart, --stop: started or stopped); 1 the artifact is missing,
partial or invalid, or an input changed while it ran; 2 usage error; 3 a precondition is missing, a `gate` job is
already running, or (--wait) none is recorded; 124 (--wait) still running; 125 (--wait) the job ended without an
exit status; 143 the gate was stopped.
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
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
import check_gate  # noqa: E402  (sibling imports after disabling bytecode)
import run_owned  # noqa: E402
from gate_common import (  # noqa: E402
    BASELINE_CACHE, DATA_SPLIT, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, PLUGIN_DIR, REQUIRED_RUNS, RUN_CASE,
    GateError, baseline_key, baseline_path, cases_sha256, check_skill_name, file_sha256, inputs_sha256,
    model_invoked, now, omitted_gold, read_baseline, read_data_split, resolve_skill,
    log_attempt, skill_sha256, split_sha256, utf8_stdio, write_gate_cases, write_json,
)

EXIT_INVALID, EXIT_PRECONDITION, EXIT_STOPPED = 1, 3, 143
JOB = "gate"
PROBE_TIMEOUT = 60  # `claude plugin eval --help` answers in seconds; bounds a CLI hung on an update or login
LOAD_CHECK_TIMEOUT = 120  # the zero-cost load check parses every case file and stops before any run
POLL_SECONDS = 0.5  # how often the gate checks its runs and the stop request
STOP_GRACE = 30  # --stop/--restart grace for the gate job: CHILD_GRACE for its eval runs, plus its record
CHILD_GRACE = 10  # an eval run's time to exit after TERM before KILL
WAIT_SECONDS = 540  # under the 600 s a Claude Code shell call may wait, so --wait returns first
ATTEMPTS = 2  # one retry absorbs a transient failure; a second leaves the artifact partial
DEFAULT_JOBS = 4  # concurrent eval runs, under a typical API account's rate limit
EVAL_TIMEOUT, CASE_TIMEOUT = 5400, 1200  # a whole plugin eval, one manual run: past these a run is hung
NOT_PROBED = "not-probed"
SCORE_RE = re.compile(r"^SCORE=(\S+)\s*$", re.MULTILINE)
EXECUTOR_RE = re.compile(r"^EXECUTOR=(\S+)\s*$", re.MULTILINE)
CLAUDE_MODEL_RE = re.compile(r"claude|anthropic|sonnet|opus|haiku", re.IGNORECASE)  # plugin eval runs only these
SCRIPTS = Path(__file__).resolve().parent
WAIT_HINT = f"python3 {SCRIPTS / 'gate.py'} --workspace {{ws}} --wait"

# Eval runs this gate job started and has not finished waiting on; TERM stops them before exiting.
_ACTIVE: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()
_STOP = threading.Event()


class GateStopped(Exception):
    """The gate job received SIGTERM (run_owned.py stop gate, or gate.py --stop / --restart)."""


def probe_plugin_eval() -> int:
    """Exit code of `claude plugin eval --help`: 127 if claude is missing, 124 on timeout."""
    if shutil.which("claude") is None:
        return 127
    try:
        return subprocess.run(["claude", "plugin", "eval", "--help"], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=PROBE_TIMEOUT).returncode
    except FileNotFoundError:
        return 127
    except subprocess.TimeoutExpired:
        return 124


def _yaml_value(path: Path, key: str) -> str | None:
    """`key: value` from a case.yaml (quotes and comments stripped), or None."""
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if match := re.match(rf"^\s*{key}:\s*(\S.*)$", line):
                return match.group(1).split(" #")[0].strip().strip("\"'") or None
    return None


def suite_cases(ws: Path) -> list[str]:
    """The case directories under plugin/evals/, as claude plugin eval runs them: one directory per case,
    named like its case.yaml `name:`. Raises GateError on a layout it would run differently."""
    plugin, evals = ws / PLUGIN_DIR, ws / PLUGIN_DIR / "evals"
    if not (plugin / ".claude-plugin" / "plugin.json").is_file() or not evals.is_dir():
        raise GateError(f"{PLUGIN_DIR} needs .claude-plugin/plugin.json and evals/ (scaffold the suite in 3.6)")
    cases = sorted(p.name for p in evals.iterdir()
                   if p.is_dir() and ((p / "case.yaml").is_file() or (p / "prompt.md").is_file()))
    nested = sorted({p.parent.relative_to(evals).as_posix() for marker in ("case.yaml", "prompt.md")
                     for p in evals.rglob(marker)
                     if len(p.relative_to(evals).parts) > 2 and p.relative_to(evals).parts[0] not in (*cases, "results")})
    if nested:
        raise GateError(f"nested eval cases {nested}: keep one directory per case directly under evals/")
    renamed = [f"{c} (name: {n})" for c in cases if (n := _yaml_value(evals / c / "case.yaml", "name")) not in (None, c)]
    if renamed:
        raise GateError(f"case.yaml `name:` differs from its directory: {', '.join(renamed)}; rename one")
    if not cases:
        raise GateError(f"no eval cases under {PLUGIN_DIR / 'evals'} (scaffold them in 3.6)")
    return cases


def load_check(ws: Path) -> None:
    """Load every case file with a zero cost ceiling: claude plugin eval names each file it cannot load (a `✗`
    line) and stops before the first run. It would otherwise skip that case and the artifact would miss it."""
    with tempfile.TemporaryDirectory(prefix="gate-load-check-") as tmp:
        cmd = ["claude", "plugin", "eval", ".", "--max-cost-usd", "0", "--ablation", "none", "--trust-plugin",
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
    """(mode, probe exit code or NOT_PROBED, why) by the phase-3.md 3.6 rule for --backend (None: the default)."""
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
    """Write the gate's case list into data-split.yaml from disk: (cases, mode, probe exit code, model-invoked).
    Plugin mode lists the suite's case directories after a load check; manual mode the gate gold standards."""
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
        if omitted := omitted_gold(ws, split.all_gold()):
            raise GateError(f"{DATA_SPLIT} leaves out gold standards found on disk: {', '.join(omitted)}; list each "
                            "under all_cases, training, validation or test (4.2), or move a file that is not a "
                            "gold standard out of that folder")
    write_gate_cases(ws / DATA_SPLIT, cases, available, source, why)
    return cases, mode, available, invoked


def run_owned_job(sdir: Path, name: str, cmd: list[str], cwd: Path, timeout: float,
                  env: dict[str, str] | None = None) -> tuple[int | None, str]:
    """Run one owned job to completion: (exit code, or None after a timeout stopped it; its log path).
    Raises GateStopped once the gate is asked to stop, leaving the job to _stop_children."""
    with _LOCK:
        if _STOP.is_set():
            raise GateStopped()
        proc = _ACTIVE[name] = run_owned.start(sdir, name, cmd, cwd=cwd, env=env)
    deadline = time.monotonic() + timeout
    while (rc := run_owned.wait(sdir, name, timeout=max(0.0, min(POLL_SECONDS, deadline - time.monotonic())),
                                proc=proc)) == run_owned.EXIT_TIMEOUT:
        if _STOP.is_set():
            raise GateStopped()
        if time.monotonic() >= deadline:
            run_owned.stop(sdir, name, grace=CHILD_GRACE, proc=proc)
            rc = None
            break
    with _LOCK:
        _ACTIVE.pop(name, None)
    return rc, run_owned.load(sdir, name).log


def _stop_children(sdir: Path) -> None:
    with _LOCK:
        _STOP.set()
        jobs = list(_ACTIVE.items())
    try:
        for outcome in run_owned.stop_many(sdir, jobs, grace=CHILD_GRACE) if jobs else []:
            print(f"gate: {outcome}", file=sys.stderr)
    except run_owned.OwnedError as exc:
        print(f"gate: could not stop its eval runs: {exc}", file=sys.stderr)


def preconditions(ws: Path, args: argparse.Namespace) -> tuple[str, list[str], str, int | str, bool]:
    """(skill, cases, mode, probe exit code, model-invoked), re-recording the case list from disk first;
    raises GateError naming the first missing piece."""
    skill = resolve_skill(ws, args.skill)
    cases, mode, available, invoked = record_cases(ws, skill, args.backend, args.force_manual)
    missing = [f"{case}: {script}" for case in cases
               if (script := _yaml_value(ws / PLUGIN_DIR / "evals" / case / "case.yaml", "scaffold_script"))
               and not (ws / PLUGIN_DIR / "evals" / case / script).is_file()]
    if missing:
        raise GateError(f"scaffold_script not found (relative to the case directory): {', '.join(missing)}")
    if mode == "manual":
        if not (ws / RUN_CASE).is_file() or not os.access(ws / RUN_CASE, os.X_OK):
            raise GateError(f"{RUN_CASE} is missing or not executable; 3.6 writes it (chmod +x)")
        if args.executor is None:
            raise GateError("manual mode: pass --executor single-call or agentic (recorded in uplift.json)")
    elif not CLAUDE_MODEL_RE.search(args.model or ""):
        print(f"gate: warning: plugin mode runs Claude models, and --model {args.model!r} names none; a build on "
              "another host (launched from Claude Code, which exports $CLAUDECODE) passes --backend manual "
              "--force-manual '<host>: <reason>'", file=sys.stderr)
    return skill, cases, mode, available, invoked


def run_plugin(ws: Path, skill: str, args: argparse.Namespace) -> None:
    """Refresh plugin/skills/<skill>/ from output/<skill>/ (refusing a path that resolves elsewhere, since it
    is deleted first), then run `claude plugin eval` with and without the skill."""
    copy = ws.resolve() / PLUGIN_DIR / "skills" / check_skill_name(skill)
    if copy.resolve() != copy:
        raise GateError(f"{copy} resolves to {copy.resolve()}, outside {PLUGIN_DIR / 'skills'}; the gate deletes and "
                        "rewrites that copy, so it refuses a symlink anywhere on the path")
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(ws / "output" / skill, copy)
    cmd = ["claude", "plugin", "eval", ".", "--ablation", "with-without", "--runs", str(args.runs),
           "-j", str(args.jobs), "--model", args.model, "--allow-tools", *args.allow_tools,
           "--scaffold", "--trust-plugin", "--no-publish", "--threshold", "0",
           "--json", str((ws / PLUGIN_ARTIFACT).resolve())]
    rc, log = run_owned_job(run_owned.state_dir(ws), "gate-plugin-eval", cmd, ws / PLUGIN_DIR, args.timeout)
    if rc is None:
        print(f"gate: claude plugin eval timed out after {args.timeout:g}s and was stopped; log: {log}", file=sys.stderr)
    elif rc != 0:
        print(f"gate: claude plugin eval exited {rc}; log: {log}", file=sys.stderr)
    skipped = [line for line in Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
               if line.startswith("✗ ")]
    if skipped:
        print(f"gate: claude plugin eval could not load {len(skipped)} case file(s):\n  " + "\n  ".join(skipped),
              file=sys.stderr)


def _one_run(ws: Path, idx: int, case: str, arm: str, run: int, args: argparse.Namespace) -> dict:
    """One manual run, retried once: {case, arm, run, score (None on failure), error, log}."""
    env = {**os.environ, "GATE_MODEL": args.model, "GATE_RUN": str(run), "GATE_EXECUTOR": args.executor}
    error, log = "not run", ""
    for attempt in range(1, ATTEMPTS + 1):
        rc, log = run_owned_job(run_owned.state_dir(ws), f"gate-c{idx:02d}-{arm}-r{run}-a{attempt}",
                                [str((ws / RUN_CASE).resolve()), case, arm], ws, args.case_timeout, env)
        text = Path(log).read_text(encoding="utf-8", errors="replace") if rc is not None else ""
        found, executor = SCORE_RE.findall(text), EXECUTOR_RE.findall(text)
        if rc is None:
            error = f"timed out after {args.case_timeout:g}s"
        elif rc != 0:
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
                return {"case": case, "arm": arm, "run": run, "score": score, "error": None, "log": log}
            error = f"SCORE={found[-1]} is outside 0..1"
    return {"case": case, "arm": arm, "run": run, "score": None, "error": error, "log": log}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def run_manual(ws: Path, cases: list[str], args: argparse.Namespace,
               inputs: dict[str, str]) -> dict[str, tuple[str, list[float]]]:
    """Run every case's arms and write uplift.json, reusing a without-skill arm cached under the case's
    baseline_key unless --fresh-baseline. Returns the complete fresh without-skill arms by case, (key, scores),
    for run_gate to cache once the inputs are unchanged."""
    keys = {case: baseline_key(ws, case, args.model, args.executor, inputs) for case in cases}
    cached = {} if args.fresh_baseline else {
        case: scores for case in cases if (scores := read_baseline(ws, keys[case], args.runs)) is not None}
    if cached:
        print(f"gate: reusing the cached without-skill arm for {len(cached)} of {len(cases)} cases "
              f"({BASELINE_CACHE.as_posix()}/; --fresh-baseline re-runs them)", file=sys.stderr)
    jobs = [(i, case, arm, run) for i, case in enumerate(cases) for arm in ("with", "without")
            for run in range(1, args.runs + 1) if not (arm == "without" and case in cached)]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(_one_run, ws, *job, args) for job in jobs]
        pending = set(futures)
        while pending and not _STOP.is_set():
            pending = futures_wait(pending, timeout=POLL_SECONDS).not_done
    if _STOP.is_set():
        raise GateStopped()
    results = sorted((f.result() for f in futures), key=lambda r: r["run"])
    out_cases, errors, fresh = [], [], {}
    for case in cases:
        arms, entry = {}, {"case": case}
        for arm in ("with", "without"):
            mine = [r for r in results if (r["case"], r["arm"]) == (case, arm)]
            arms[arm] = [r["score"] for r in mine if r["error"] is None]
            errors += [{k: r[k] for k in ("case", "arm", "run", "error", "log")} for r in mine if r["error"]]
        if case in cached:
            arms["without"] = cached[case]
            entry["without_cache"] = {"key": keys[case], "sha256": file_sha256(baseline_path(ws, keys[case]))}
        elif len(arms["without"]) == args.runs:
            fresh[case] = (keys[case], arms["without"])
        delta = _mean(arms["with"]) - _mean(arms["without"]) if arms["with"] and arms["without"] else None
        out_cases.append({**entry, **arms, "delta": None if delta is None else round(delta, 4)})
    means = {arm: _mean([s for c in out_cases for s in c[arm]]) for arm in ("with", "without")}
    write_json(ws / MANUAL_ARTIFACT, {
        "cases": out_cases,
        "summary": {"model": args.model, "executor": args.executor, "runs_per_case": args.runs,
                    "in_sample": args.in_sample,
                    **{f"mean_{arm}": None if m is None else round(m, 4) for arm, m in means.items()},
                    "mean_delta": None if None in means.values() else round(round(means["with"], 4)
                                                                            - round(means["without"], 4), 4)},
        "partial": bool(errors), "errors": errors,
    })
    for err in errors:
        print(f"gate: {err['case']} {err['arm']} run {err['run']}: {err['error']} (log {err['log']})", file=sys.stderr)
    return fresh


def _input_hashes(ws: Path, skill: str, cases: list[str]) -> dict:
    """What the gate binds its run to, taken at the start and compared again at the end."""
    return {"skill_sha256": skill_sha256(ws, skill), "cases_sha256": cases_sha256(ws, cases),
            "split_sha256": split_sha256(read_data_split(ws / DATA_SPLIT)),
            "run_case_sha256": file_sha256(ws / RUN_CASE), "inputs_sha256": inputs_sha256(ws)}


def run_gate(ws: Path, args: argparse.Namespace) -> int:
    """The gate itself; runs inside the owned `gate` job."""
    signal.signal(signal.SIGTERM, lambda *_: _STOP.set())
    try:
        skill, cases, mode, available, invoked = preconditions(ws, args)
        artifact = PLUGIN_ARTIFACT if mode == "plugin" else MANUAL_ARTIFACT
        record = {"mode": mode, "backend": args.backend or default_backend(), "plugin_eval_available": available,
                  "model_invoked": invoked, "force_manual": args.force_manual, "fresh_baseline": args.fresh_baseline,
                  "skill": skill, "model": args.model, "runs": args.runs, "in_sample": args.in_sample,
                  "executor": args.executor, "cases": cases, "artifact": artifact.as_posix(),
                  **_input_hashes(ws, skill, cases), "started": now(), "finished": None}
        write_json(ws / GATE_RECORD, record)
        if (ws / artifact).exists():  # set aside, so a stale artifact can never pass
            os.replace(ws / artifact, ws / artifact.with_name(artifact.stem + ".prev.json"))
        print(f"gate: mode={mode} (plugin eval probe exit {available}, model-invoked={invoked}), "
              f"{len(cases)} cases x {args.runs} runs per arm", file=sys.stderr)
        log_attempt(ws, "gate")
        fresh = run_manual(ws, cases, args, record["inputs_sha256"]) if mode == "manual" else {}
        if mode == "plugin":
            run_plugin(ws, skill, args)
    except GateError as exc:
        log_attempt(ws, "gate", str(exc))
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    except GateStopped:
        _stop_children(run_owned.state_dir(ws))
        print("gate: stopped before it finished; gate.json records no finished run", file=sys.stderr)
        return EXIT_STOPPED

    after = _input_hashes(ws, skill, cases)
    if changed := sorted(key for key in after if after[key] != record[key]):
        write_json(ws / GATE_RECORD, record | {"changed_during_run": changed})
        print(f"gate: GATE INVALID: gate inputs changed while the gate ran ({', '.join(changed)}); gate.json records "
              "no finished run; re-run 5.0 without editing the workspace meanwhile", file=sys.stderr)
        return EXIT_INVALID
    for case, (key, scores) in fresh.items():
        baseline_path(ws, key).parent.mkdir(parents=True, exist_ok=True)
        write_json(baseline_path(ws, key), {"key": key, "case": case, "model": args.model, "executor": args.executor,
                                            "runs": scores, "written": now()})
    write_json(ws / GATE_RECORD, record | {"artifact_sha256": file_sha256(ws / artifact), "finished": now()})
    verdict = check_gate.validate(ws, skill)
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
    argv = [sys.executable, "-B", str(SCRIPTS / "gate.py"), "--workspace", str(ws), "--allow-tools", *args.allow_tools]
    for key in ("skill", "backend", "force_manual", "model", "runs", "jobs", "executor", "timeout", "case_timeout"):
        if getattr(args, key) is not None:
            argv += [f"--{key.replace('_', '-')}", str(getattr(args, key))]
    return argv + [f"--{key.replace('_', '-')}" for key in ("in_sample", "fresh_baseline") if getattr(args, key)]


def _elapsed(started: str) -> str:
    try:
        seconds = int((datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())
    except (TypeError, ValueError):
        return "an unknown time"
    return f"{seconds // 3600}h{seconds // 60 % 60:02d}m" if seconds >= 3600 else f"{seconds // 60}m{seconds % 60:02d}s"


def wait_gate(ws: Path, timeout: float | None, proc: subprocess.Popen | None = None) -> int:
    """Block on the recorded `gate` job for up to `timeout` seconds (None: until it ends). Finished: print its
    log, return its exit code. Still running: print one progress line, return 124. No job recorded: 3."""
    sdir = run_owned.state_dir(ws)
    try:
        rec = run_owned.load(sdir, JOB)
    except run_owned.OwnedError:
        print(f"gate: no `{JOB}` job is recorded; start one with python3 {SCRIPTS / 'gate.py'} --detach "
              "--model <model> [options]", file=sys.stderr)
        return EXIT_PRECONDITION
    rc = run_owned.wait(sdir, JOB, timeout=timeout, proc=proc)
    log = Path(rec.log).read_text(encoding="utf-8", errors="replace") if Path(rec.log).is_file() else ""
    if rc == run_owned.EXIT_TIMEOUT:
        last = next((line.strip() for line in reversed(log.splitlines()) if line.strip()), "")
        print(f"gate: still running after {_elapsed(rec.started)}{f' (last: {last})' if last else ''}; call --wait "
              f"again (exit 124), not run_owned.py status: {WAIT_HINT.format(ws=ws)}", file=sys.stderr)
        return rc
    sys.stdout.write(log)
    if rc == run_owned.EXIT_NO_STATUS:
        print(f"gate: the `{JOB}` job ended without recording an exit status; see {rec.log}", file=sys.stderr)
    return rc


def supervise(ws: Path, args: argparse.Namespace) -> int:
    """Start the gate as the owned job `gate` (stopping a live one first with --restart), then wait for it
    unless --detach or --restart is given without --wait."""
    sdir = run_owned.state_dir(ws)
    live = _running(sdir)
    if live and not args.restart:
        print(f"gate: a `{JOB}` job is already running (pgid {live['pgid']}, log {live['log']}).\n"
              f"  wait:      {WAIT_HINT.format(ws=ws)}\n"
              f"  restart:   python3 {SCRIPTS / 'gate.py'} --restart <the same options>", file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        if args.detach or args.restart:
            preconditions(ws, args)
    except GateError as exc:
        log_attempt(ws, "gate", str(exc))
        print(f"gate: {exc}\ngate: not started{'; the running gate was left as it is' if live else ''}",
              file=sys.stderr)
        return EXIT_PRECONDITION
    if live:
        print(f"gate: {run_owned.stop(sdir, JOB, grace=STOP_GRACE)}", file=sys.stderr)
    proc = run_owned.start(sdir, JOB, _job_argv(ws, args), cwd=ws, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    rec = run_owned.load(sdir, JOB)
    print(f"gate: started as owned job `{JOB}` (pgid {rec.pgid}); log {rec.log}", file=sys.stderr)
    if args.wait:
        return wait_gate(ws, args.wait_timeout, proc)
    if args.detach or args.restart:
        print(f"gate: block until it exits (its exit code is the gate's; 124: call it again):\n  {WAIT_HINT.format(ws=ws)}",
              file=sys.stderr)
        return 0
    print(f"gate: waiting; if this call is cut off the gate keeps running: {WAIT_HINT.format(ws=ws)}", file=sys.stderr)
    try:
        return wait_gate(ws, None, proc)
    except KeyboardInterrupt:
        print(f"\ngate: still running in the background: {WAIT_HINT.format(ws=ws)}", file=sys.stderr)
        return 130


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
    add = parser.add_argument
    add("command", nargs="?", choices=["record"], help="write the case list into data-split.yaml, then exit")
    add("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    add("--skill", help="skill name under output/ (default: the only one)")
    add("--backend", choices=["plugin", "manual"], help="default: plugin inside Claude Code when it can run, else manual")
    add("--force-manual", metavar="'<host>: <reason>'", help="with --backend manual, on another host inheriting $CLAUDECODE")
    add("--model", help="the model the skill targets, pinned for both arms (required to start a gate)")
    add("--runs", type=int, default=REQUIRED_RUNS, help=f"runs per arm per case (default and required {REQUIRED_RUNS})")
    add("--jobs", type=int, default=DEFAULT_JOBS, help=f"concurrent runs, 1-8 (default {DEFAULT_JOBS})")
    add("--allow-tools", nargs="+", default=["Bash", "Write"], help="plugin mode: gated tools (default: Bash Write)")
    add("--in-sample", action="store_true", help="the gate cases are also training cases (3-9 gold standards)")
    add("--executor", choices=["single-call", "agentic"], help="manual mode (required there): how run-case.sh runs")
    add("--fresh-baseline", action="store_true", help="manual mode: re-run cached without-skill arms")
    add("--timeout", type=float, default=EVAL_TIMEOUT, help=f"plugin mode: seconds for the eval (default {EVAL_TIMEOUT})")
    add("--case-timeout", type=float, default=CASE_TIMEOUT, help=f"manual mode: seconds per run (default {CASE_TIMEOUT})")
    add("--detach", action="store_true", help="start the `gate` job and return; then block with --wait")
    add("--restart", action="store_true", help="stop the recorded `gate` job, then start a new one detached")
    add("--stop", action="store_true", help="stop the recorded `gate` job and its eval runs")
    add("--wait", action="store_true", help="block on the `gate` job (after starting it with --detach/--restart)")
    add("--wait-timeout", type=float, default=WAIT_SECONDS, help=f"--wait: seconds before exit 124 (default {WAIT_SECONDS})")
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
    sdir = run_owned.state_dir(ws)
    try:
        if args.stop:
            print(f"gate: {run_owned.stop(sdir, JOB, grace=STOP_GRACE)}" if _running(sdir) else
                  f"gate: no running `{JOB}` job", file=sys.stderr)
            return 0
        if bare_wait:
            return wait_gate(ws, args.wait_timeout)
        return run_gate(ws, args) if _inside_gate_job(ws) else supervise(ws, args)
    except run_owned.OwnedError as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION


if __name__ == "__main__":
    sys.exit(main())
