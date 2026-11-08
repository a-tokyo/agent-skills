#!/usr/bin/env python3
"""Score the skill as it is now and log it as an experiment line, so the metric-first ship rule can read it.

    rescore.py [--workspace DIR] [--skill NAME] [--wait-timeout 270]   # exit 124: issue it again, it re-attaches

Run it whenever output/<skill>/ changed after its last scored experiment (a fix after the gate or the panel, the
4.6 description pass) and `check_gate.py` says no kept experiment scored the skill as it is now. It:
  1. needs output/<skill>/ committed: with uncommitted changes it prints the `git commit` to run and exits 1;
  2. exits 0 at once, printing `ALREADY SCORED: ...`, when a kept experiment already scored the skill as it is
     now (check_gate.py's ship_metric, the same test the SHIP RULE applies), so a second run changes nothing;
  3. runs work/evaluation/evaluate.sh with no argument (`metric`, the training split) and once per held-out case
     (`held_out`, their mean: the `validation` list, else under leave-one-out the all_cases entry missing from
     `training`; phase-4.md 4.2), reading `METRIC overall_score=<x>` from each;
  4. logs the attempt to work/evaluation/gate-attempts.jsonl (a failure that a commit cannot fix lets
     `Verdict: UNSHIPPED` stand at --finish), and on success appends {"experiment": <next>, "commit": HEAD, "metric", "held_out", "status": "rescore", "scores"} to the
     experiment log check_gate.py reads (./autoresearch.jsonl while the loop runs, else work/experiments/) and prints
     `RESCORED: ...`. It never rotates the held-out case and never edits the skill.

The scoring runs as the owned job `rescore` (work/pids/, scripts/run_owned.py): the call blocks up to 270 s, under
the 600 s a Claude Code shell call may wait (timeout 600000), and exits 124 while it still runs. Issue the same
command again: it re-attaches to the running job, or reports its result once it has ended, and never starts a
second scoring run. The loop guard does not stop it: a re-score is not a loop experiment.

Under the lite profile (verdict-rules.json lite rescores_per_commit) each commit is re-scored once: a second call on
a commit that already has a rescore attempt prints `RESCORE REFUSED` and the next step (check_gate.py --next).

Exit codes: 0 scored or already scored, 1 cannot score (no evaluate.sh, uncommitted skill, no git, no held-out
case, an evaluate.sh run without a METRIC overall_score line) or refused (lite), 2 usage error, 3 another `rescore` job state error,
124 still running (issue it again).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import check_gate  # noqa: E402  (sibling imports after disabling bytecode)
import run_owned  # noqa: E402
from gate_common import (  # noqa: E402
    DATA_SPLIT, GateError, is_number, lite_rules, log_attempt, profile, read_attempts, read_data_split, resolve_skill, utf8_stdio,
)

EVALUATE = Path("work") / "evaluation" / "evaluate.sh"
METRIC = re.compile(r"^METRIC ([\w.-]+)=(-?[0-9.]+(?:[eE][-+]?\d+)?)\s*$", re.MULTILINE)
STATUS = "rescore"
JOB = "rescore"


class CannotScore(Exception):
    """Why the skill cannot be scored now; the message says what to do. `logged`: a failure the builder cannot fix
    by committing, recorded as a `rescore` attempt so --finish lets UNSHIPPED stand."""

    def __init__(self, reason: str, logged: bool = True) -> None:
        super().__init__(reason)
        self.logged = logged
        self.commit: str | None = None  # set once HEAD is known


def held_out_cases(workspace: Path) -> list[str]:
    split = read_data_split(workspace / DATA_SPLIT)
    if split.gold_error:
        raise CannotScore(f"{DATA_SPLIT}: {split.gold_error}")
    if split.gold.get("validation"):
        return list(split.gold["validation"])
    training = set(split.gold.get("training", []))
    cases = [c for c in split.gold.get("all_cases", []) if c not in training]
    if not cases:
        raise CannotScore(f"{DATA_SPLIT} names no held-out case: no `validation` list and no all_cases entry "
                          "missing from `training` (phase-4.md 4.2)")
    return cases


def evaluate(workspace: Path, *case: str) -> tuple[float, dict[str, float]]:
    """(overall_score, the other METRIC values) of one evaluate.sh run, from the workspace root."""
    proc = subprocess.run(["bash", EVALUATE.as_posix(), *case], cwd=workspace, capture_output=True,
                          encoding="utf-8", errors="replace")
    found = {k: float(v) for k, v in METRIC.findall(proc.stdout)}
    if proc.returncode != 0 or "overall_score" not in found:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
        run = " ".join([EVALUATE.as_posix(), *case])
        raise CannotScore(f"`{run}` exited {proc.returncode} without a `METRIC overall_score=<x>` line; fix "
                          f"evaluate.sh (phase-3.md 3.4). Its output ends:\n{tail}")
    score = found.pop("overall_score")
    return score, found


def head(workspace: Path, skill: str) -> str:
    top = check_gate._git(workspace, "rev-parse", "--show-toplevel")
    rev = check_gate._git(workspace, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if top is None or top.returncode != 0 or rev is None or rev.returncode != 0:
        raise CannotScore(f"{workspace} is not a git repository with a commit; the ship rule matches a score to "
                          "its commit (phase-3.md 3.5 commits the baseline)")
    commit = rev.stdout.strip()
    if check_gate._changed_since(workspace, commit, skill) is not None:
        raise CannotScore(f"output/{skill}/ has uncommitted changes; commit them, then run this again:\n"
                          f"git -C {workspace} add -A -- output/{skill} && git -C {workspace} commit -m "
                          f"\"rescore {skill}\" -- output/{skill}", logged=False)
    return commit


def log_path(workspace: Path) -> Path:
    """The log check_gate.py reads (the first of EXPERIMENT_LOGS that exists), else work/experiments/'s."""
    found = [workspace / p for p in check_gate.EXPERIMENT_LOGS if (workspace / p).is_file()]
    return found[0] if found else workspace / check_gate.EXPERIMENT_LOGS[-1]


def prepare(workspace: Path, skill: str | None) -> tuple[str, list[str], str, str | None]:
    """(skill, held-out cases, HEAD, the ALREADY SCORED reading or None): every check that needs no evaluate.sh run."""
    if not (workspace / EVALUATE).is_file():
        raise CannotScore(f"no {EVALUATE} in {workspace}: Phase 3.4 writes it before anything is scored")
    try:
        name = resolve_skill(workspace, skill)
        cases = held_out_cases(workspace)
    except GateError as exc:
        raise CannotScore(str(exc)) from exc
    commit = head(workspace, name)
    value, how = check_gate.ship_metric(workspace, name)
    return name, cases, commit, (how if value is not None else None)


def refused(workspace: Path, commit: str) -> str | None:
    """The lite profile rescores each commit once: the refusal, with the next step, when a rescore attempt (scored
    or not) is already logged for `commit`; else None."""
    if profile(workspace) != "lite" or sum(1 for a in read_attempts(workspace) if a.get("step") == "rescore"
                                           and a.get("commit") == commit) < lite_rules()["rescores_per_commit"]:
        return None
    return (f"RESCORE REFUSED: commit {commit[:12]} was already re-scored (lite profile: one rescore per skill commit; "
            "a second run cannot change the score). Next: "
            f"python3 {Path(__file__).resolve().parent / 'check_gate.py'} --workspace {workspace} --next")


def rescore(workspace: Path, skill: str | None) -> tuple[bool, dict | str]:
    """(scored now, the logged line) or (False, the ALREADY SCORED reading)."""
    name, cases, commit, already = prepare(workspace, skill)
    if already is not None:
        return False, already
    try:
        return True, score(workspace, name, cases, commit)
    except CannotScore as exc:
        exc.commit = commit
        raise


def score(workspace: Path, name: str, cases: list[str], commit: str) -> dict:
    """Run the metric and every held-out case once and append the experiment line."""
    metric, scores = evaluate(workspace)
    held = [evaluate(workspace, case)[0] for case in cases]
    _, experiments, _ = check_gate._experiments(workspace)
    numbers = [e["experiment"] for e in experiments if is_number(e.get("experiment"))]
    line = {"experiment": int(max(numbers, default=-1)) + 1, "commit": commit, "metric": round(metric, 4),
            "held_out": round(sum(held) / len(held), 4), "status": STATUS, "scores": scores,
            "hypothesis": f"re-score of output/{name}/ at its current commit", "held_out_cases": cases}
    path = log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return line


def _inside_job(workspace: Path) -> bool:
    """True when this process is the `rescore` job's command: its token matches the job's record."""
    token = os.environ.get("RUN_OWNED_TOKEN")
    if os.environ.get("RUN_OWNED_JOB") != JOB or not token:
        return False
    try:
        return run_owned.load(run_owned.state_dir(workspace), JOB).token == token
    except run_owned.OwnedError:
        return False


def score_now(workspace: Path, skill: str | None) -> int:
    """The scoring itself, printed: inside the `rescore` job (or a direct library call)."""
    try:
        scored, result = rescore(workspace, skill)
    except CannotScore as exc:
        if exc.logged and (workspace / "work").is_dir():
            log_attempt(workspace, "rescore", str(exc).splitlines()[0], **({"commit": exc.commit} if exc.commit else {}))
        print(f"CANNOT RESCORE: {exc}")
        return 1
    if not scored:
        print(f"ALREADY SCORED: {result}; nothing to do")
        return 0
    log_attempt(workspace, "rescore", commit=result["commit"])
    print(f"RESCORED: experiment {result['experiment']} commit {result['commit'][:12]} metric {result['metric']:.3f} "
          f"held_out {result['held_out']:.3f}; logged to {log_path(workspace).relative_to(workspace).as_posix()}")
    return 0


def supervise(workspace: Path, skill: str | None, timeout: float = run_owned.WAIT_SECONDS) -> int:
    """Check what needs no scoring run here; score in the owned job `rescore` and block on it (270 s). The same
    command again re-attaches to that job, or reports its result once, instead of scoring twice."""
    sdir = run_owned.state_dir(workspace)
    argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--workspace", str(workspace)]
    argv += ["--skill", skill] if skill else []
    try:  # re-attach to a running job, or once to a failed one no call reported; a scored commit reads ALREADY SCORED
        rec, state = run_owned.load(sdir, JOB), run_owned.status(sdir, JOB)
        attach = rec.cmd == argv and (state["state"] == "running" or (
            state["state"] == "exited" and state["exit_code"] != 0 and not rec.reported))
        if not attach and not rec.reported and state["state"] != "running":
            run_owned.mark_reported(sdir, JOB)
    except run_owned.OwnedError:
        attach = False
    if not attach:
        try:
            _, _, commit, already = prepare(workspace, skill)
            if refusal := refused(workspace, commit):
                print(refusal)
                return 1
        except CannotScore as exc:
            if exc.logged and (workspace / "work").is_dir():
                log_attempt(workspace, "rescore", str(exc).splitlines()[0])
            print(f"CANNOT RESCORE: {exc}")
            return 1
        if already is not None:
            print(f"ALREADY SCORED: {already}; nothing to do")
            return 0
    command = " ".join(["python3", str(Path(__file__).resolve()), "--workspace", str(workspace)]
                       + (["--skill", skill] if skill else []))
    try:
        return run_owned.run_attached(sdir, JOB, argv, cwd=workspace, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                      timeout=timeout, again=f"issue the same command again: {command}")
    except run_owned.OwnedError as exc:
        print(f"rescore: {exc}", file=sys.stderr)
        return run_owned.EXIT_STATE


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the only one)")
    parser.add_argument("--wait-timeout", type=float, default=run_owned.WAIT_SECONDS,
                        help=f"seconds to block before exit 124 (default {run_owned.WAIT_SECONDS:g})")
    args = parser.parse_args(argv)
    if args.wait_timeout <= 0:
        parser.error("--wait-timeout must be > 0")
    workspace = args.workspace.resolve()
    if _inside_job(workspace):
        return score_now(workspace, args.skill)
    return supervise(workspace, args.skill, args.wait_timeout)


if __name__ == "__main__":
    sys.exit(main())
