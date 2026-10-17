#!/usr/bin/env python3
"""Validate the Phase 5.0 gate artifact before the panel (SKILL.md 5.2) starts.

Exit 0 only if the canonical artifact for the gate's mode exists at its path, covers exactly the
gate cases recorded under `plugin_eval` in work/evaluation/data-split.yaml, has at least
--min-runs completed runs per arm per case, and is not partial:

    plugin mode   work/evaluation/plugin-eval.json   ("partial": false, written by claude plugin eval)
    manual mode   work/evaluation/uplift.json        (the manual with/without protocol)

The mode comes from work/evaluation/gate.json, written by gate.py. Without it (a gate not run
through gate.py) the mode is derived the way gate.py derives it: plugin when data-split.yaml
records `plugin_eval_available: 0` and the skill is model-invoked, otherwise manual.

Prints one verdict line: the delta summary on success, the precise reason on failure.

    check_gate.py [--workspace DIR] [--skill NAME] [--min-runs N] [--json]

Exit codes: 0 valid, 1 invalid, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
from gate_common import (  # noqa: E402  (sibling import after disabling bytecode)
    DATA_SPLIT, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, REQUIRED_RUNS, GateError,
    model_invoked, read_data_split, resolve_skill,
)


@dataclass
class Verdict:
    valid: bool
    mode: str | None
    artifact: str | None
    reason: str
    cases: int = 0
    mean_delta: float | None = None
    non_positive: list[str] = field(default_factory=list)

    def line(self) -> str:
        if not self.valid:
            return f"GATE INVALID: {self.reason}"
        delta = "n/a" if self.mean_delta is None else f"{self.mean_delta:+.3f}"
        weak = ", ".join(self.non_positive) or "none"
        return (f"GATE VALID: mode={self.mode} artifact={self.artifact} cases={self.cases} "
                f"mean delta={delta}; delta<=0: {weak}")


def _mode(workspace: Path, skill: str | None, available: int | str | None) -> str:
    record = workspace / GATE_RECORD
    if record.is_file():
        try:
            mode = json.loads(record.read_text()).get("mode")
        except (json.JSONDecodeError, AttributeError) as exc:
            raise GateError(f"{GATE_RECORD} is not a JSON object: {exc}") from exc
        if mode not in ("plugin", "manual"):
            raise GateError(f"{GATE_RECORD} records mode {mode!r}; expected plugin or manual")
        return mode
    name = resolve_skill(workspace, skill)
    invoked = model_invoked(workspace / "output" / name / "SKILL.md")
    return "plugin" if available == 0 and invoked else "manual"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _coverage(recorded: list[str], names: list[str]) -> str | None:
    if len(set(names)) != len(names):
        return "artifact lists a case more than once"
    missing = [c for c in recorded if c not in names]
    extra = [n for n in names if n not in recorded]
    if missing or extra:
        return (f"artifact cases differ from data-split.yaml plugin_eval: "
                f"missing {missing or 'none'}, unrecorded {extra or 'none'}")
    return None


def _plugin(data: dict, recorded: list[str], min_runs: int) -> tuple[str | None, dict[str, float | None]]:
    if data.get("partial") is not False:
        reason = data.get("partialReason") or "partial is not false"
        return f"plugin eval artifact is partial ({reason}); re-run the gate", {}
    cases = data.get("cases")
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        return "plugin-eval.json has no cases list", {}
    deltas: dict[str, float | None] = {}
    for case in cases:
        name = str(case.get("name"))
        arms = case.get("arms") or {}
        for arm in ("with", "without"):
            runs = arms.get(arm)
            done = [r for r in runs if isinstance(r, dict) and not r.get("error")] if isinstance(runs, list) else []
            if len(done) < min_runs:
                return f"case {name!r} arm {arm!r} has {len(done)} completed runs; {min_runs} required", {}
        delta = (case.get("aggregates") or {}).get("delta")
        deltas[name] = float(delta) if isinstance(delta, (int, float)) else None
    return _coverage(recorded, [str(c.get("name")) for c in cases]), deltas


def _manual(data: dict, recorded: list[str], min_runs: int) -> tuple[str | None, dict[str, float | None]]:
    if data.get("partial") is True:
        return "uplift.json is partial; re-run the gate", {}
    cases = data.get("cases")
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        return "uplift.json has no cases list (schema: references/benchmark-standard.md)", {}
    deltas: dict[str, float | None] = {}
    for case in cases:
        name = str(case.get("case"))
        scores: dict[str, list[float]] = {}
        for arm in ("with", "without"):
            runs = case.get(arm)
            ok = isinstance(runs, list) and all(
                isinstance(s, (int, float)) and not isinstance(s, bool) and 0 <= s <= 1 for s in runs)
            if not ok:
                return f"case {name!r} arm {arm!r} is not a list of scores in 0..1", {}
            if len(runs) < min_runs:
                return f"case {name!r} arm {arm!r} has {len(runs)} runs; {min_runs} required", {}
            scores[arm] = [float(s) for s in runs]
        deltas[name] = _mean(scores["with"]) - _mean(scores["without"])
    return _coverage(recorded, [str(c.get("case")) for c in cases]), deltas


def validate(workspace: Path, skill: str | None = None, min_runs: int = REQUIRED_RUNS) -> Verdict:
    try:
        split = read_data_split(workspace / DATA_SPLIT)
        if split.plugin_eval is None:
            return Verdict(False, None, None, f"{DATA_SPLIT}: {split.plugin_eval_error}")
        if not split.plugin_eval:
            return Verdict(False, None, None, f"{DATA_SPLIT}: plugin_eval records no gate cases")
        mode = _mode(workspace, skill, split.plugin_eval_available)
    except GateError as exc:
        return Verdict(False, None, None, str(exc))

    rel = PLUGIN_ARTIFACT if mode == "plugin" else MANUAL_ARTIFACT
    path = workspace / rel
    if not path.is_file():
        return Verdict(False, mode, str(rel), f"mode {mode}: {rel} not found; run the gate (5.0)")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return Verdict(False, mode, str(rel), f"{rel} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        return Verdict(False, mode, str(rel), f"{rel} is not a JSON object")

    check = _plugin if mode == "plugin" else _manual
    reason, deltas = check(data, split.plugin_eval, min_runs)
    if reason:
        return Verdict(False, mode, str(rel), reason)
    known = [d for d in deltas.values() if d is not None]
    weak = [name for name, d in deltas.items() if d is not None and d <= 0]
    return Verdict(True, mode, str(rel), "ok", cases=len(deltas), mean_delta=_mean(known), non_positive=weak)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the only one)")
    parser.add_argument("--min-runs", type=int, default=REQUIRED_RUNS,
                        help=f"completed runs required per arm per case (default {REQUIRED_RUNS})")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON instead of a line")
    args = parser.parse_args(argv)
    if args.min_runs < 1:
        parser.error("--min-runs must be at least 1")
    verdict = validate(args.workspace.resolve(), args.skill, args.min_runs)
    print(json.dumps(asdict(verdict)) if args.json else verdict.line())
    return 0 if verdict.valid else 1


if __name__ == "__main__":
    sys.exit(main())
