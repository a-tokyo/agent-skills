#!/usr/bin/env python3
"""Validate the Phase 5.0 gate artifact before the panel (SKILL.md 5.2) starts.

Exit 0 only if work/evaluation/gate.json records a finished gate.py run whose hashes still match
output/<skill>/, the recorded cases, run-case.sh, the scorer and fixture files under input/ and
work/evaluation/ (gate_common.inputs_sha256) and the artifact itself — any edit after the gate is
"gate inputs changed since the gate; re-run 5.0" — and the canonical artifact for its mode exists
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

On success it prints `GATE VALID: ...` with the mean delta and the delta<=0 list over gold cases
only, then every Skill indicator, whatever its case's class: how often the skill fired in the with-arm
(should-trigger) or stayed quiet in both arms (near miss). On failure it prints `GATE INVALID: <reason>`.
--json prints the same verdict as JSON, with a stable `code`.

--finish checks the build's end state instead (SKILL.md 5.1): BENCHMARK.md at the build root has a
`Verdict: <label>` line naming SHIP, SHIP WITH CAVEATS or UNSHIPPED, no `gate` job is running, a
SHIP or SHIP WITH CAVEATS verdict stands on a gate that validates now and whose current `GATE VALID:` line
BENCHMARK.md carries (whitespace aside) and on a panel that scored the skill as it is now (the receipt
--panel wrote holds this output/<skill>/ hash), a plain SHIP has no gold delta<=0 case (SKILL.md 5.4: it
caps the verdict at SHIP WITH CAVEATS), and an UNSHIPPED verdict follows a gate attempt: gate.py logged a record or gate
step in work/evaluation/gate-attempts.jsonl, and a gate run started (gate.json) or a logged error stopped it. It prints `BUILD FINISHED: verdict <label>` or `BUILD UNFINISHED: <reason>`.

--panel validates the gate like the default and, when it is valid, writes the panel receipt
work/experiments/panel.json: the sha256 of output/<skill>/ the panel is about to score (SKILL.md 5.2).

    check_gate.py [--workspace DIR] [--skill NAME] [--json] [--panel | --finish]

Tested on macOS, Linux and Windows. Exit codes: 0 valid (--finish: finished), 1 invalid (unfinished), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import locale
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
import run_owned  # noqa: E402  (sibling imports after disabling bytecode)
from gate_common import (  # noqa: E402
    DATA_SPLIT, GATE_ATTEMPTS, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, REQUIRED_RUNS, RUN_CASE, GateError,
    cases_sha256, check_skill_name, file_sha256, inputs_sha256, read_attempts, read_data_split, skill_sha256, utf8_stdio,
    write_json,
)

BENCHMARK = Path("BENCHMARK.md")
PANEL_RECEIPT = Path("work") / "experiments" / "panel.json"
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


def _gate_record(workspace: Path, skill: str | None, recorded: list[str]) -> dict:
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
    if not all(key in record for key in ("run_case_sha256", "inputs_sha256", "artifact_sha256")):
        raise Invalid("gate_unhashed", f"{GATE_RECORD} has no scorer, input or artifact hash (an older "
                                       "gate.py); re-run 5.0")
    then, now = record["inputs_sha256"] or {}, inputs_sha256(workspace)
    changed = sorted(p for p in then.keys() | now.keys() if then.get(p) != now.get(p))
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
            if _is_near_miss(grader):
                runs = done["with"] + done["without"]
                result.near_miss = f"{_passed(runs, grader.get('name'))}/{len(runs)}"
            else:
                result.trigger = f"{_passed(done['with'], grader.get('name'))}/{len(done['with'])}"
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
        record = _gate_record(workspace, skill, split.plugin_eval)
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
    """gate.py logged a record or gate step, and either a gate run started (gate.json) or a logged
    GateError stopped it."""
    attempts = read_attempts(workspace)
    return bool(attempts) and ((workspace / GATE_RECORD).is_file() or any(a.get("error") for a in attempts))


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
        return False, verdict, ("UNSHIPPED needs a gate attempt: no gate.py record or gate run is logged in "
                                f"{GATE_ATTEMPTS}; run 3.6 and 5.0 (a build that does not ship still runs the gate)")
    if verdict != "UNSHIPPED":
        gate = validate(workspace, skill)
        if not gate.valid:
            return False, verdict, f"{verdict} needs a valid gate: {gate.line()}; return to 5.0"
        if _spaced(gate.line()) not in _spaced(text):
            return False, verdict, (f"{BENCHMARK} lacks check_gate.py's current GATE VALID line beside the {verdict} "
                                    f"verdict; copy it in verbatim: {gate.line()}")
        if verdict == "SHIP" and gate.non_positive:
            return False, verdict, (f"gold delta<=0 on {', '.join(gate.non_positive)} caps the verdict at SHIP WITH "
                                    "CAVEATS (5.4); write that, with why the case cannot benefit")
        scored = _panel_scored(workspace)
        if scored is None:
            return False, verdict, (f"{verdict} needs the panel receipt {PANEL_RECEIPT.as_posix()}: run 5.2 "
                                    "(check_gate.py --panel, then the panel)")
        if scored != skill_sha256(workspace, gate.skill):
            return False, verdict, (f"the panel scored an earlier output/{gate.skill}/ than the one gated now "
                                    f"({PANEL_RECEIPT.as_posix()}); re-run 5.2 on this skill, then 5.3 and 5.4")
    return True, verdict, "ok"


def _panel_scored(workspace: Path) -> str | None:
    """The output/<skill>/ hash the panel receipt records, or None when there is no readable receipt."""
    try:
        receipt = json.loads((workspace / PANEL_RECEIPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scored = receipt.get("skill_sha256") if isinstance(receipt, dict) else None
    return scored if isinstance(scored, str) else None


def write_panel_receipt(workspace: Path, verdict: Verdict) -> Path:
    path = workspace / PANEL_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"skill": verdict.skill, "skill_sha256": skill_sha256(workspace, verdict.skill),
                      "gate_line": verdict.line(), "written": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return path


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    parser.add_argument("--skill", help="skill name under output/ (default: the one gate.json records)")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON instead of a line")
    parser.add_argument("--panel", action="store_true",
                        help="5.2: validate, then record the skill hash the panel scores in work/experiments/panel.json")
    parser.add_argument("--finish", action="store_true",
                        help="check the build's end state (BENCHMARK.md verdict, no live gate) instead of the gate")
    args = parser.parse_args(argv)
    if args.panel and args.finish:
        parser.error("--panel and --finish are separate steps (5.2 and 5.4)")
    if args.finish:
        done, label, reason = finish(args.workspace.resolve(), args.skill)
        line = f"BUILD FINISHED: verdict {label}" if done else f"BUILD UNFINISHED: {reason}"
        print(json.dumps({"finished": done, "verdict": label, "reason": reason}) if args.json else line)
        return 0 if done else 1
    workspace = args.workspace.resolve()
    verdict = validate(workspace, args.skill)
    print(json.dumps(asdict(verdict)) if args.json else verdict.line())
    if args.panel and verdict.valid:
        path = write_panel_receipt(workspace, verdict)
        print(f"PANEL RECEIPT: {path.relative_to(workspace).as_posix()} records output/{verdict.skill}/ for the "
              "panel; any change to the skill after this means re-running 5.2", file=sys.stderr)
    return 0 if verdict.valid else 1


if __name__ == "__main__":
    sys.exit(main())
