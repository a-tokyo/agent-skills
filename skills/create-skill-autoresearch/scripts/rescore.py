#!/usr/bin/env python3
"""Score the skill as it is now and log it as an experiment line, so the metric-first ship rule can read it.

    rescore.py [--workspace DIR] [--skill NAME]

Run it whenever output/<skill>/ changed after its last scored experiment (a fix after the gate or the panel, the
4.6 description pass) and `check_gate.py` says no kept experiment scored the skill as it is now. It:
  1. needs output/<skill>/ committed: with uncommitted changes it prints the `git commit` to run and exits 1;
  2. exits 0 at once, printing `ALREADY SCORED: ...`, when a kept experiment already scored the skill as it is
     now (check_gate.py's ship_metric, the same test the SHIP RULE applies), so a second run changes nothing;
  3. runs work/evaluation/evaluate.sh with no argument (`metric`, the training split) and once per held-out case
     (`held_out`, their mean: the `validation` list, else under leave-one-out the all_cases entry missing from
     `training`; phase-4.md 4.2), reading `METRIC overall_score=<x>` from each;
  4. appends {"experiment": <next>, "commit": HEAD, "metric", "held_out", "status": "rescore", "scores"} to the
     experiment log check_gate.py reads (./autoresearch.jsonl while the loop runs, else work/experiments/) and prints
     `RESCORED: ...`. It never rotates the held-out case and never edits the skill.

The loop guard does not stop it: a re-score is not a loop experiment.

Exit codes: 0 scored or already scored, 1 cannot score (no evaluate.sh, uncommitted skill, no git, no held-out
case, an evaluate.sh run without a METRIC overall_score line), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import check_gate  # noqa: E402  (sibling imports after disabling bytecode)
from gate_common import DATA_SPLIT, GateError, is_number, read_data_split, resolve_skill, utf8_stdio  # noqa: E402

EVALUATE = Path("work") / "evaluation" / "evaluate.sh"
METRIC = re.compile(r"^METRIC ([\w.-]+)=(-?[0-9.]+(?:[eE][-+]?\d+)?)\s*$", re.MULTILINE)
STATUS = "rescore"


class CannotScore(Exception):
    """Why the skill cannot be scored now; the message says what to do."""


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
                          f"\"rescore {skill}\" -- output/{skill}")
    return commit


def log_path(workspace: Path) -> Path:
    """The log check_gate.py reads (the first of EXPERIMENT_LOGS that exists), else work/experiments/'s."""
    found = [workspace / p for p in check_gate.EXPERIMENT_LOGS if (workspace / p).is_file()]
    return found[0] if found else workspace / check_gate.EXPERIMENT_LOGS[-1]


def rescore(workspace: Path, skill: str | None) -> tuple[bool, dict | str]:
    """(scored now, the logged line) or (False, the ALREADY SCORED reading)."""
    if not (workspace / EVALUATE).is_file():
        raise CannotScore(f"no {EVALUATE} in {workspace}: Phase 3.4 writes it before anything is scored")
    try:
        name = resolve_skill(workspace, skill)
        cases = held_out_cases(workspace)
    except GateError as exc:
        raise CannotScore(str(exc)) from exc
    commit = head(workspace, name)
    value, how = check_gate.ship_metric(workspace, name)
    if value is not None:
        return False, how
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
    return True, line


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the only one)")
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    try:
        scored, result = rescore(workspace, args.skill)
    except CannotScore as exc:
        print(f"CANNOT RESCORE: {exc}")
        return 1
    if not scored:
        print(f"ALREADY SCORED: {result}; nothing to do")
        return 0
    print(f"RESCORED: experiment {result['experiment']} commit {result['commit'][:12]} metric {result['metric']:.3f} "
          f"held_out {result['held_out']:.3f}; logged to {log_path(workspace).relative_to(workspace).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
