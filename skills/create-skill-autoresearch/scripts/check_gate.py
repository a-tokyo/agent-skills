#!/usr/bin/env python3
"""Validate the Phase 5.0 gate artifact before the panel (SKILL.md 5.2) starts.

Exit 0 only if work/evaluation/gate.json records a finished gate.py run whose skill and case hashes
still match output/<skill>/ and the recorded cases, and the canonical artifact for its mode exists
at its path, covers exactly the gate cases recorded under `plugin_eval` in
work/evaluation/data-split.yaml, has at least --min-runs completed runs per arm per case, and
records `"partial": false`:

    plugin mode   work/evaluation/plugin-eval.json   (written by claude plugin eval)
    manual mode   work/evaluation/uplift.json        (the manual with/without protocol)

An artifact without a finished gate.py record never passes: only gate.py writes the artifact.

Cases are classed by their graders. A case is gold when it has any score-bearing grader (anything but
`tool_used` on `Skill`), whatever Skill indicator it also carries; every manual-mode case is gold.
Only a case whose sole graders are `tool_used` on `Skill` is a should-trigger indicator (`max: 0`: a
near miss). Every gold case needs a numeric delta, and when data-split.yaml records gold standards
(`all_cases`, `training`, `validation` or `test`) at least one case must be gold.

On success it prints `GATE VALID: ...` with the mean delta and the delta<=0 list over gold cases
only; indicators are reported apart, as how often the skill fired / stayed quiet. On failure it
prints `GATE INVALID: <reason>`. --json prints the same verdict as JSON, with a stable `code`.

    check_gate.py [--workspace DIR] [--skill NAME] [--min-runs N] [--json]

Tested on macOS and Linux. Exit codes: 0 valid, 1 invalid, 2 usage error.
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
    cases_sha256, read_data_split, skill_sha256,
)


class Invalid(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class Verdict:
    valid: bool
    mode: str | None
    artifact: str | None
    reason: str
    code: str = "ok"
    cases: int = 0
    gold_cases: int = 0
    mean_delta: float | None = None
    non_positive: list[str] = field(default_factory=list)
    triggers: dict[str, str] = field(default_factory=dict)
    near_misses: dict[str, str] = field(default_factory=dict)

    def line(self) -> str:
        if not self.valid:
            return f"GATE INVALID: {self.reason}"
        delta = "n/a" if self.mean_delta is None else f"{self.mean_delta:+.3f}"

        def listed(items: dict[str, str]) -> str:
            return ", ".join(f"{name} {runs}" for name, runs in items.items()) or "none"

        return (f"GATE VALID: mode={self.mode} artifact={self.artifact} cases={self.cases} "
                f"gold={self.gold_cases} mean gold delta={delta}; gold delta<=0: {', '.join(self.non_positive) or 'none'}; "
                f"trigger fired (with arm): {listed(self.triggers)}; near-miss quiet: {listed(self.near_misses)}")


@dataclass
class CaseResult:
    name: str
    delta: float | None
    kind: str = "gold"
    indicator: str | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _gate_record(workspace: Path, skill: str | None, recorded: list[str]) -> dict:
    path = workspace / GATE_RECORD
    if not path.is_file():
        raise Invalid("no_gate_record", f"no {GATE_RECORD}: the artifact was not written by a gate run; "
                                        "run scripts/gate.py (5.0)")
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise Invalid("bad_gate_record", f"{GATE_RECORD} is not valid JSON: {exc}") from exc
    if not isinstance(record, dict) or record.get("mode") not in ("plugin", "manual"):
        raise Invalid("bad_gate_record", f"{GATE_RECORD} records no mode (plugin or manual)")
    if not record.get("finished"):
        raise Invalid("gate_unfinished", f"{GATE_RECORD} records no finished run (stopped, failed or still "
                                         "running); re-run 5.0")
    if not record.get("skill_sha256") or not record.get("cases_sha256"):
        raise Invalid("gate_unhashed", f"{GATE_RECORD} has no skill or case hash (an older gate.py); re-run 5.0")
    name = record.get("skill")
    if skill and name != skill:
        raise Invalid("bad_gate_record", f"{GATE_RECORD} gated skill {name!r}, not {skill!r}; re-run 5.0")
    if not isinstance(name, str) or not (workspace / "output" / name / "SKILL.md").is_file():
        raise Invalid("bad_gate_record", f"{GATE_RECORD} names skill {name!r}, which is not under output/")
    if skill_sha256(workspace, name) != record["skill_sha256"]:
        raise Invalid("stale_skill", f"skill changed since the gate; re-run 5.0 (output/{name}/ no longer "
                                     f"matches {GATE_RECORD})")
    if cases_sha256(workspace, recorded) != record["cases_sha256"]:
        raise Invalid("stale_cases", "gate cases changed since the gate; re-run 5.0 (data-split.yaml "
                                     "plugin_eval or the suite under plugin/evals/)")
    return record


def _coverage(recorded: list[str], names: list[str]) -> None:
    if len(set(names)) != len(names):
        raise Invalid("coverage", "artifact lists a case more than once")
    missing = [c for c in recorded if c not in names]
    extra = [n for n in names if n not in recorded]
    if missing or extra:
        raise Invalid("coverage", f"artifact cases differ from data-split.yaml plugin_eval: "
                                  f"missing {missing or 'none'}, unrecorded {extra or 'none'}")


def _is_skill_grader(grader: object) -> bool:
    return (isinstance(grader, dict) and grader.get("type") == "tool_used"
            and (grader.get("config") or {}).get("tool") == "Skill")


def _indicator_grader(case: dict) -> dict | None:
    """The case's `tool_used` grader on `Skill` when that is all the case grades: a should-trigger or
    near-miss indicator. A case with any other grader is gold, whatever Skill indicator it also has."""
    graders = case.get("graders") or []
    if graders and all(_is_skill_grader(g) for g in graders):
        return next((g for g in graders if (g.get("config") or {}).get("max") == 0), graders[0])
    return None


def _passed(runs: list, grader: str) -> int:
    return sum(1 for run in runs for g in (run.get("graders") or [])
               if isinstance(g, dict) and g.get("name") == grader and g.get("passed") is True)


def _plugin(data: dict, recorded: list[str], min_runs: int) -> list[CaseResult]:
    if data.get("partial") is not False:
        reason = data.get("partialReason") or "partial is not false"
        raise Invalid("partial", f"plugin eval artifact is partial ({reason}); re-run the gate")
    cases = data.get("cases")
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        raise Invalid("bad_artifact", "plugin-eval.json has no cases list")
    results = []
    for case in cases:
        name = str(case.get("name"))
        arms = case.get("arms") or {}
        done = {}
        for arm in ("with", "without"):
            runs = arms.get(arm)
            done[arm] = [r for r in runs if isinstance(r, dict) and not r.get("error")] if isinstance(runs, list) else []
            if len(done[arm]) < min_runs:
                raise Invalid("too_few_runs", f"case {name!r} arm {arm!r} has {len(done[arm])} completed runs; "
                                              f"{min_runs} required")
        delta = (case.get("aggregates") or {}).get("delta")
        numeric = isinstance(delta, (int, float)) and not isinstance(delta, bool)
        result = CaseResult(name, float(delta) if numeric else None)
        grader = _indicator_grader(case)
        if grader is None and not numeric:
            raise Invalid("no_delta", f"gold case {name!r} has no numeric with/without delta ({delta!r}); "
                                      "re-run the gate")
        if grader is not None:
            if (grader.get("config") or {}).get("max") == 0:
                runs = done["with"] + done["without"]
                result.kind, result.indicator = "near_miss", f"{_passed(runs, grader.get('name'))}/{len(runs)}"
            else:
                result.kind = "trigger"
                result.indicator = f"{_passed(done['with'], grader.get('name'))}/{len(done['with'])}"
        results.append(result)
    _coverage(recorded, [r.name for r in results])
    return results


def _manual(data: dict, recorded: list[str], min_runs: int) -> list[CaseResult]:
    if data.get("partial") is not False:
        state = "partial" if data.get("partial") is True else "records no \"partial\": false"
        raise Invalid("partial", f"uplift.json is {state}; re-run the gate")
    cases = data.get("cases")
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        raise Invalid("bad_artifact", "uplift.json has no cases list (schema: references/benchmark-standard.md)")
    results = []
    for case in cases:
        name = str(case.get("case"))
        scores: dict[str, list[float]] = {}
        for arm in ("with", "without"):
            runs = case.get(arm)
            ok = isinstance(runs, list) and all(
                isinstance(s, (int, float)) and not isinstance(s, bool) and 0 <= s <= 1 for s in runs)
            if not ok:
                raise Invalid("bad_artifact", f"case {name!r} arm {arm!r} is not a list of scores in 0..1")
            if len(runs) < min_runs:
                raise Invalid("too_few_runs", f"case {name!r} arm {arm!r} has {len(runs)} runs; {min_runs} required")
            scores[arm] = [float(s) for s in runs]
        results.append(CaseResult(name, _mean(scores["with"]) - _mean(scores["without"])))
    _coverage(recorded, [r.name for r in results])
    return results


def validate(workspace: Path, skill: str | None = None, min_runs: int = REQUIRED_RUNS) -> Verdict:
    mode = rel = None
    try:
        try:
            split = read_data_split(workspace / DATA_SPLIT)
        except GateError as exc:
            raise Invalid("no_split", str(exc)) from exc
        if split.plugin_eval is None:
            raise Invalid("no_split", f"{DATA_SPLIT}: {split.plugin_eval_error}")
        if not split.plugin_eval:
            raise Invalid("no_split", f"{DATA_SPLIT}: plugin_eval records no gate cases")
        mode = _gate_record(workspace, skill, split.plugin_eval)["mode"]
        rel = PLUGIN_ARTIFACT if mode == "plugin" else MANUAL_ARTIFACT
        path = workspace / rel
        if not path.is_file():
            raise Invalid("no_artifact", f"mode {mode}: {rel} not found; run the gate (5.0)")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise Invalid("bad_artifact", f"{rel} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise Invalid("bad_artifact", f"{rel} is not a JSON object")
        results = (_plugin if mode == "plugin" else _manual)(data, split.plugin_eval, min_runs)
        if split.gold_recorded and not any(r.kind == "gold" for r in results):
            raise Invalid("no_gold", f"{DATA_SPLIT} records gold standards but no gate case is a gold case "
                                     "(every case grades only `tool_used` on Skill); 3.6 adds one per gate "
                                     "gold standard")
    except Invalid as exc:
        return Verdict(False, mode, None if rel is None else str(rel), exc.reason, code=exc.code)

    gold = [r for r in results if r.kind == "gold"]
    return Verdict(
        True, mode, str(rel), "ok", cases=len(results), gold_cases=len(gold),
        mean_delta=_mean([r.delta for r in gold if r.delta is not None]),
        non_positive=[r.name for r in gold if r.delta is not None and r.delta <= 0],
        triggers={r.name: r.indicator for r in results if r.kind == "trigger"},
        near_misses={r.name: r.indicator for r in results if r.kind == "near_miss"},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the one gate.json records)")
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
