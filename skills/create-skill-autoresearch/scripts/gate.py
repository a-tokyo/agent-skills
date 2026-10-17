#!/usr/bin/env python3
"""Run the Phase 5.0 eval gate end to end and write its canonical artifact.

Mode: `plugin` when `claude plugin eval --help` exits 0 and the skill is model-invoked, otherwise
`manual`. Both run the gate cases recorded under `plugin_eval` in work/evaluation/data-split.yaml,
in the foreground, each job through run_owned.py (its own process group, stopped on timeout).

  plugin  refresh work/evaluation/plugin/skills/<skill>/ from output/<skill>/, then run
          `claude plugin eval .` with and without the skill -> work/evaluation/plugin-eval.json
  manual  call work/evaluation/run-case.sh <case> <with|without> --runs times per arm per case;
          it prints SCORE=<0..1> as its last SCORE line -> work/evaluation/uplift.json
          (a failed run is retried once; a second failure leaves the artifact partial)

It records the mode and the probe's exit code in work/evaluation/gate.json, moves a previous
artifact aside to <name>.prev.json before running so a stale file can never pass, ends by running
check_gate.py, and writes nothing outside work/evaluation/ and work/pids/.

    gate.py --model <model> [--workspace DIR] [--skill NAME] [--runs 3] [--jobs 4]
            [--allow-tools Bash Write] [--in-sample] [--executor single-call|agentic]
            [--timeout 5400] [--case-timeout 1200]

run-case.sh receives GATE_MODEL and GATE_RUN in its environment and runs from the workspace root.

Exit codes: 0 complete and valid; 1 the gate ran but the artifact is missing, partial or invalid
(the message says why); 2 usage error; 3 a precondition is missing (suite, cases, run-case.sh).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
import check_gate  # noqa: E402  (sibling imports after disabling bytecode)
import run_owned  # noqa: E402
from gate_common import (  # noqa: E402
    DATA_SPLIT, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, PLUGIN_DIR, REQUIRED_RUNS, RUN_CASE,
    GateError, model_invoked, read_data_split, resolve_skill, write_json,
)

EXIT_INVALID = 1
EXIT_PRECONDITION = 3
PROBE_TIMEOUT = 60
SCORE_RE = re.compile(r"^SCORE=(\S+)\s*$", re.MULTILINE)
ATTEMPTS = 2


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
    """Run one owned job to completion; returns (exit code or None on timeout, log path)."""
    proc = run_owned.start(sdir, name, cmd, cwd=cwd, env=env)
    rc = run_owned.wait(sdir, name, timeout=timeout, proc=proc)
    log = run_owned.load(sdir, name).log
    if rc == run_owned.EXIT_TIMEOUT:
        run_owned.stop(sdir, name, grace=10, proc=proc)
        return None, log
    return rc, log


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
        results = list(pool.map(lambda j: _one_run(ws, *j, args), jobs))

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the only one)")
    parser.add_argument("--model", required=True, help="the model the skill targets (pinned for both arms)")
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
    args = parser.parse_args(argv)
    if args.runs < 1 or not 1 <= args.jobs <= 8:
        parser.error("--runs must be >= 1 and --jobs 1-8")

    ws = args.workspace.resolve()
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
                  "cases": split.plugin_eval, "artifact": str(artifact), "started": _now(), "finished": None}
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

    record["finished"] = _now()
    write_json(ws / GATE_RECORD, record)
    verdict = check_gate.validate(ws, skill, REQUIRED_RUNS)
    print(verdict.line())
    return 0 if verdict.valid else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
