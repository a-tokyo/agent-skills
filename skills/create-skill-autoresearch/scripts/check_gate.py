#!/usr/bin/env python3
"""Validate the Phase 5.0 gate artifact before the panel (SKILL.md 5.2) starts.

Exit 0 only if work/evaluation/gate.json records a finished gate.py run whose hashes still match
output/<skill>/, the recorded cases, the data-split.yaml splits, run-case.sh, the scorer and fixture files
under input/ and work/evaluation/ (gate_common.inputs_sha256) and the artifact itself — any edit after the gate
is "gate inputs changed since the gate; re-run 5.0" — and the canonical artifact for its mode exists
at its path, covers exactly the gate cases recorded under `plugin_eval` in
work/evaluation/data-split.yaml, has at least 3 completed runs per arm per case (criterion 7; no flag lowers it), and
records `"partial": false`:

    plugin mode   work/evaluation/plugin-eval.json   (written by claude plugin eval)
    manual mode   work/evaluation/uplift.json        (the manual with/without protocol)

An artifact without a finished gate.py record never passes: only gate.py writes the artifact.

Cases are classed by their graders. A case is gold when it has any score-bearing grader (anything but
`tool_used` on `Skill`), whatever Skill indicator it also carries; every manual-mode case is gold.
Only a case whose sole graders are `tool_used` on `Skill` is a should-trigger indicator (`max: 0`: a
near miss). Every gold case needs a numeric delta, and when data-split.yaml records gold standards
(`all_cases`, `training`, `validation` or `test`) at least one case must be gold.

Every Skill indicator gates too (SKILL.md 5.0): a should-trigger grader must pass in every with-arm run
and a near miss (`max: 0`) in every run of both arms; otherwise the gate is invalid (a description defect,
back to 4.6). On success it prints `GATE VALID: ...` with the mean delta and the delta<=0 list over gold
cases only, then every Skill indicator's pass count. On failure it prints `GATE INVALID: <reason>`.
--json prints the same verdict as JSON, with a stable `code`.

The panel (SKILL.md 5.2-5.3, scripts/panel.py) runs on a valid gate only: --panel checks it before the
verifiers start; each verifier, after scoring, writes its role receipt with --panel-receipt ROLE SCORES_JSON
(--synthesis for the synthesis round); --consensus computes the verdict the three receipts allow, by the
thresholds in references/verdict-rules.json, and prints `PANEL CONSENSUS: ...`.

--finish checks the build's end state instead (SKILL.md 5.1): BENCHMARK.md at the build root has a
`Verdict: <label>` line naming SHIP, SHIP WITH CAVEATS or UNSHIPPED, and no `gate` job is running. A SHIP or
SHIP WITH CAVEATS verdict stands on a gate that validates now, on a consensus of three role receipts for the
skill and rubric as they are now that allows that label, and BENCHMARK.md carries the current `GATE VALID:`
and `PANEL CONSENSUS:` lines (whitespace aside); a plain SHIP has no gold delta<=0 case (SKILL.md 5.4 caps it
at SHIP WITH CAVEATS). An UNSHIPPED verdict follows a gate attempt (a `gate` step in
work/evaluation/gate-attempts.jsonl: a run that started, or a GateError the gate step raised), and carries
those two lines too whenever the gate validates and a current consensus exists, so the reason stands beside
the label. It prints `BUILD FINISHED: verdict <label>` or `BUILD UNFINISHED: <reason>`.

    check_gate.py [--workspace DIR] [--skill NAME] [--json]
                  [--panel | --panel-receipt ROLE SCORES_JSON [--synthesis] | --consensus | --finish]

Tested on macOS, Linux and Windows. Exit codes: 0 valid (--finish: finished), 1 invalid (unfinished), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import locale
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
import panel  # noqa: E402  (sibling imports after disabling bytecode)
import run_owned  # noqa: E402
from gate_common import (  # noqa: E402
    DATA_SPLIT, GATE_ATTEMPTS, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, REQUIRED_RUNS, RUN_CASE, GateError,
    cases_sha256, check_skill_name, file_sha256, inputs_sha256, read_attempts, read_data_split, skill_sha256, split_sha256,
    utf8_stdio,
)

BENCHMARK = Path("BENCHMARK.md")
TERMINAL = {"SHIP", "SHIP WITH CAVEATS", "UNSHIPPED"}
VERDICT_LINE = re.compile(r"(?im)^[#>*_\s-]*verdict[*_\s]*[:—–-][*_\s`]*"
                          r"(UNSHIPPED|SHIP[ _]+WITH[ _]+CAVEATS|ITERATE|BLOCK|ESCALATE|SHIP)\b")


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
    skill: str | None = None

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
    trigger: str | None = None
    near_miss: str | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _gate_record(workspace: Path, skill: str | None, split) -> dict:
    recorded = split.plugin_eval
    path = workspace / GATE_RECORD
    if not path.is_file():
        raise Invalid("no_gate_record", f"no {GATE_RECORD}: the artifact was not written by a gate run; "
                                        "run scripts/gate.py (5.0)")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
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
    if skill is not None and name != skill:
        raise Invalid("bad_gate_record", f"{GATE_RECORD} gated skill {name!r}, not {skill!r}; re-run 5.0")
    try:
        check_skill_name(name)
    except GateError as exc:
        raise Invalid("bad_gate_record", f"{GATE_RECORD}: {exc}") from exc
    if not (workspace / "output" / name / "SKILL.md").is_file():
        raise Invalid("bad_gate_record", f"{GATE_RECORD} names skill {name!r}, which is not under output/")
    if skill_sha256(workspace, name) != record["skill_sha256"]:
        raise Invalid("stale_skill", f"skill changed since the gate; re-run 5.0 (output/{name}/ no longer "
                                     f"matches {GATE_RECORD})")
    if cases_sha256(workspace, recorded) != record["cases_sha256"]:
        raise Invalid("stale_cases", "gate cases changed since the gate; re-run 5.0 (data-split.yaml "
                                     "plugin_eval or the suite under plugin/evals/)")
    if not all(key in record for key in ("run_case_sha256", "inputs_sha256", "artifact_sha256", "split_sha256")):
        raise Invalid("gate_unhashed", f"{GATE_RECORD} has no scorer, input, split or artifact hash (an older "
                                       "gate.py); re-run 5.0")
    then, now = record["inputs_sha256"] or {}, inputs_sha256(workspace)
    changed = sorted(p for p in then.keys() | now.keys() if then.get(p) != now.get(p))
    if split_sha256(split) != record["split_sha256"]:
        changed.insert(0, f"{DATA_SPLIT.as_posix()} (its splits)")
    if file_sha256(workspace / RUN_CASE) != record["run_case_sha256"]:
        changed.insert(0, RUN_CASE.as_posix())
    if changed:
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        raise Invalid("stale_inputs", f"gate inputs changed since the gate; re-run 5.0 ({shown})")
    return record


def _coverage(recorded: list[str], names: list[str]) -> None:
    if len(set(names)) != len(names):
        raise Invalid("coverage", "artifact lists a case more than once")
    missing = [c for c in recorded if c not in names]
    extra = [n for n in names if n not in recorded]
    if missing or extra:
        hint = ("; a case plugin eval cannot load is skipped — `gate.py record` names it" if missing else "")
        raise Invalid("coverage", f"artifact cases differ from data-split.yaml plugin_eval: "
                                  f"missing {missing or 'none'}, unrecorded {extra or 'none'}{hint}")


def _is_skill_grader(grader: object) -> bool:
    return (isinstance(grader, dict) and grader.get("type") == "tool_used"
            and (grader.get("config") or {}).get("tool") == "Skill")


def _is_near_miss(grader: dict) -> bool:
    return (grader.get("config") or {}).get("max") == 0


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
        graders = [g for g in case.get("graders") or [] if isinstance(g, dict)]
        skill = [g for g in graders if _is_skill_grader(g)]
        if graders and len(skill) == len(graders):
            result.kind = "near_miss" if any(_is_near_miss(g) for g in skill) else "trigger"
        elif not numeric:
            raise Invalid("no_delta", f"gold case {name!r} has no numeric with/without delta ({delta!r}); "
                                      "re-run the gate")
        for grader in skill:
            runs = done["with"] + done["without"] if _is_near_miss(grader) else done["with"]
            passed = _passed(runs, grader.get("name"))
            if _is_near_miss(grader):
                result.near_miss = f"{passed}/{len(runs)}"
            else:
                result.trigger = f"{passed}/{len(runs)}"
            if passed < len(runs):
                code, what = (("near_miss_fired", f"near miss {name!r} fired the skill in {len(runs) - passed}/"
                               f"{len(runs)} runs (either arm)") if _is_near_miss(grader) else
                              ("trigger_failed", f"case {name!r}: the skill did not fire in {len(runs) - passed}/"
                               f"{len(runs)} with-arm runs"))
                raise Invalid(code, f"{what}; a description defect: return to 4.6, then re-run 5.0")
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


def validate(workspace: Path, skill: str | None = None) -> Verdict:
    mode = rel = None
    try:
        try:
            if skill is not None:
                check_skill_name(skill)
        except GateError as exc:
            raise Invalid("bad_skill", str(exc)) from exc
        try:
            split = read_data_split(workspace / DATA_SPLIT)
        except GateError as exc:
            raise Invalid("no_split", str(exc)) from exc
        if split.plugin_eval is None:
            raise Invalid("no_split", f"{DATA_SPLIT}: {split.plugin_eval_error}")
        if not split.plugin_eval:
            raise Invalid("no_split", f"{DATA_SPLIT}: plugin_eval records no gate cases")
        record = _gate_record(workspace, skill, split)
        mode = record["mode"]
        rel = PLUGIN_ARTIFACT if mode == "plugin" else MANUAL_ARTIFACT
        path = workspace / rel
        if not path.is_file():
            raise Invalid("no_artifact", f"mode {mode}: {rel} not found; run the gate (5.0)")
        if file_sha256(path) != record["artifact_sha256"]:
            raise Invalid("stale_inputs", f"gate inputs changed since the gate; re-run 5.0 ({rel} is not the "
                                          "artifact the gate wrote)")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise Invalid("bad_artifact", f"{rel} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise Invalid("bad_artifact", f"{rel} is not a JSON object")
        results = (_plugin if mode == "plugin" else _manual)(data, split.plugin_eval, REQUIRED_RUNS)
        if split.gold_recorded and not any(r.kind == "gold" for r in results):
            raise Invalid("no_gold", f"{DATA_SPLIT} records gold standards but no gate case is a gold case "
                                     "(every case grades only `tool_used` on Skill); 3.6 adds one per gate "
                                     "gold standard")
    except Invalid as exc:
        return Verdict(False, mode, None if rel is None else rel.as_posix(), exc.reason, code=exc.code)

    gold = [r for r in results if r.kind == "gold"]
    return Verdict(
        True, mode, rel.as_posix(), "ok", cases=len(results), gold_cases=len(gold),
        mean_delta=_mean([r.delta for r in gold if r.delta is not None]),
        non_positive=[r.name for r in gold if r.delta is not None and r.delta <= 0],
        triggers={r.name: r.trigger for r in results if r.trigger is not None},
        near_misses={r.name: r.near_miss for r in results if r.near_miss is not None},
        skill=record["skill"],
    )


def _read_benchmark(path: Path) -> str:
    """UTF-8, else the locale's encoding: an editor on Windows may save BENCHMARK.md in its code page."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def _spaced(text: str) -> str:
    return " ".join(text.split())


def _gate_attempted(workspace: Path) -> bool:
    """gate.py logged a `gate` step: a gate run started, or a GateError stopped the gate step. A failed
    `gate.py record` (3.6) is not a gate attempt."""
    return any(a.get("step") == "gate" for a in read_attempts(workspace))


def finish(workspace: Path, skill: str | None = None) -> tuple[bool, str | None, str]:
    """(finished, verdict, reason) for the build's end state: BENCHMARK.md at the build root states a
    terminal verdict on a `Verdict: <label>` line, no `gate` job is live, and a SHIP or SHIP WITH CAVEATS
    verdict stands on a gate that still validates, whose current GATE VALID line BENCHMARK.md carries."""
    bench = workspace / BENCHMARK
    if not bench.is_file():
        return False, None, f"no {BENCHMARK} at the build root; carry Phase 5 through 5.4 to a terminal verdict"
    text = _read_benchmark(bench)
    match = VERDICT_LINE.search(text)
    if not match:
        return False, None, f"{BENCHMARK} has no `Verdict: <label>` line (SHIP, SHIP WITH CAVEATS or UNSHIPPED)"
    verdict = re.sub(r"[ _]+", " ", match.group(1).upper())
    if verdict not in TERMINAL:
        return False, verdict, (f"verdict {verdict} is not terminal: return to Phase 4, or with no budget left "
                                "write UNSHIPPED (5.4)")
    try:
        live = run_owned.status(run_owned.state_dir(workspace), "gate")["state"] == "running"
    except run_owned.OwnedError:
        live = False
    if live:
        return False, verdict, "the `gate` job is still running; wait for it or run gate.py --stop"
    if verdict == "UNSHIPPED" and not _gate_attempted(workspace):
        return False, verdict, (f"UNSHIPPED needs a gate attempt: no gate run or gate-step error is logged in "
                                f"{GATE_ATTEMPTS}; run 3.6 and 5.0 (a build that does not ship still runs the gate)")
    gate = validate(workspace, skill)
    if not gate.valid:
        if verdict == "UNSHIPPED":
            return True, verdict, "ok"
        return False, verdict, f"{verdict} needs a valid gate: {gate.line()}; return to 5.0"
    if _spaced(gate.line()) not in _spaced(text):
        return False, verdict, (f"{BENCHMARK} lacks check_gate.py's current GATE VALID line beside the {verdict} "
                                f"verdict; copy it in verbatim: {gate.line()}")
    if verdict == "SHIP" and gate.non_positive:
        return False, verdict, (f"gold delta<=0 on {', '.join(gate.non_positive)} caps the verdict at SHIP WITH "
                                "CAVEATS (5.4); write that, with why the case cannot benefit")
    consensus, why = panel.recorded(workspace, skill_sha256(workspace, gate.skill))
    if consensus is None:
        if verdict == "UNSHIPPED":
            return True, verdict, "ok"
        return False, verdict, f"{verdict} needs the panel's consensus on this skill: {why}"
    if panel.RANK[verdict] > panel.RANK[consensus.allowed]:
        return False, verdict, (f"the panel's consensus allows at most {consensus.allowed} ({consensus.line()}); "
                                f"write that verdict, or return to Phase 4 and re-run 5.0-5.3")
    if _spaced(consensus.line()) not in _spaced(text):
        return False, verdict, (f"{BENCHMARK} lacks the panel's current PANEL CONSENSUS line beside the {verdict} "
                                f"verdict; copy it in verbatim: {consensus.line()}")
    return True, verdict, "ok"


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the one gate.json records)")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON instead of a line")
    step = parser.add_mutually_exclusive_group()
    step.add_argument("--panel", action="store_true", help="5.2: validate the gate before the panel starts")
    step.add_argument("--panel-receipt", nargs=2, metavar=("ROLE", "SCORES_JSON"),
                      help=f"5.2: a verifier's role receipt after it scores ({', '.join(panel.ROLES)})")
    step.add_argument("--consensus", action="store_true",
                      help="5.3: compute the panel's verdict from the three role receipts")
    step.add_argument("--finish", action="store_true",
                      help="check the build's end state (BENCHMARK.md verdict, no live gate) instead of the gate")
    parser.add_argument("--synthesis", action="store_true",
                        help="with --panel-receipt: the synthesis round's revised_scores (5.3)")
    args = parser.parse_args(argv)
    if args.synthesis and not args.panel_receipt:
        parser.error("--synthesis goes with --panel-receipt")
    workspace = args.workspace.resolve()
    if args.finish:
        done, label, reason = finish(workspace, args.skill)
        line = f"BUILD FINISHED: verdict {label}" if done else f"BUILD UNFINISHED: {reason}"
        print(json.dumps({"finished": done, "verdict": label, "reason": reason}) if args.json else line)
        return 0 if done else 1
    verdict = validate(workspace, args.skill)
    if not (args.panel_receipt or args.consensus) or not verdict.valid:
        print(json.dumps(asdict(verdict)) if args.json else verdict.line())
    if not verdict.valid:
        return 1
    skill_sha = skill_sha256(workspace, verdict.skill)
    try:
        if args.panel_receipt:
            role, source = args.panel_receipt
            path = panel.write_receipt(workspace, role, Path(source), verdict.skill, skill_sha, args.synthesis)
            print(f"PANEL RECEIPT: {path.relative_to(workspace).as_posix()} ({role}, round "
                  f"{2 if args.synthesis else 1}) binds these scores to output/{verdict.skill}/ as it is now")
        elif args.consensus:
            path, result = panel.write_consensus(workspace, verdict.skill, skill_sha)
            print(result.line())
        elif args.panel:
            print(f"panel: after scoring, each verifier runs `check_gate.py --panel-receipt <role> <scores.json>` "
                  f"({', '.join(panel.ROLES)}); then `check_gate.py --consensus` (5.3)", file=sys.stderr)
    except GateError as exc:
        print(f"PANEL INCOMPLETE: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
