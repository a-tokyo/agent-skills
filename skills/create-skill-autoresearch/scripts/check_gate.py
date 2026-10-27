#!/usr/bin/env python3
"""Validate the Phase 5.0 gate, the panel's receipts and consensus, and the build's end state.

    check_gate.py [--workspace DIR] [--skill NAME] [--json]
                  [--panel | --panel-receipt ROLE SCORES_JSON [--synthesis] | --consensus | --finish | --fast-path
                   | --loop]

No flag: exit 0 only when work/evaluation/gate.json records a finished gate.py run whose hashes still match the
skill, cases, splits, run-case.sh, gate inputs and artifact, and the mode's artifact (plugin-eval.json or
uplift.json) covers exactly data-split.yaml's plugin_eval with >= 3 completed runs per arm, `"partial": false`,
a numeric delta per gold case and every Skill indicator passing (phase-5.md 5.0). Prints `GATE VALID: ...` or
`GATE INVALID: <reason>`; --json the same verdict with a stable `code`.
--panel, --panel-receipt, --consensus: the panel on a valid gate (SKILL.md 5.2-5.3; scripts/panel.py).
--finish: BENCHMARK.md's verdict stands (SKILL.md 5.1, 5.4): `BUILD FINISHED: ...` or `BUILD UNFINISHED: <reason>`.
--fast-path: at the end of 3.5, may the build skip the Phase 4 loop (phase-3.md 3.5): `FAST PATH: ...` or
`NO FAST PATH: <reason>`; it reads the baseline, the validation check and git, never the gate.
--loop: is the Phase 4 loop over (phase-4.md 4.3; the loop guard in hooks/kill_guard.py asks the same):
`LOOP DONE: <reason>` or `LOOP OPEN: <reason>`.

Exit codes: 0 valid (--finish: finished; --fast-path: take it; --loop: done), 1 invalid (unfinished; no fast path;
open), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
import panel  # noqa: E402  (sibling imports after disabling bytecode)
import run_owned  # noqa: E402
from gate_common import (  # noqa: E402
    BASELINE_KEY, DATA_SPLIT, GATE_ATTEMPTS, GATE_RECORD, MANUAL_ARTIFACT, PLUGIN_ARTIFACT, REQUIRED_RUNS, RUN_CASE,
    GateError, baseline_key, baseline_path, cases_sha256, check_skill_name, file_sha256, inputs_sha256, is_number,
    json_lines, read_attempts, read_baseline, read_data_split, read_json, resolve_skill, skill_sha256, split_sha256,
    utf8_stdio,
)

BENCHMARK = Path("BENCHMARK.md")
EXPERIMENT_LOGS = (Path("autoresearch.jsonl"), Path("work") / "experiments" / "autoresearch.jsonl")
TERMINAL = {"SHIP", "SHIP WITH CAVEATS", "UNSHIPPED"}
VALIDATION_DROP = 0.05  # phase-4.md 4.2: validation this far below training is the overfitting warning
NOISE_FLOOR = 0.01  # phase-4.md Loop Conventions: the noise threshold's absolute minimum
DISCARDED = {"discard", "discarded", "revert", "reverted", "crash"}  # experiment statuses that are not kept
REOPENED = {"ITERATE", "BLOCK"}  # panel consensus labels that send the build back to Phase 4
VERDICT_LINE = re.compile(r"(?im)^[#>*_\s-]*verdict[*_\s]*[:—–-][*_\s`]*"
                          r"(UNSHIPPED|SHIP[ _]+WITH[ _]+CAVEATS|ITERATE|BLOCK|ESCALATE|SHIP)\b")


class Invalid(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


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
    kind: str = "gold"  # gold | trigger | near_miss
    trigger: str | None = None
    near_miss: str | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _gate_record(workspace: Path, skill: str | None, split) -> dict:
    """gate.json, when it records a finished run whose hashes all still match the workspace."""
    if not (workspace / GATE_RECORD).is_file():
        raise Invalid("no_gate_record", f"no {GATE_RECORD}: the artifact was not written by a gate run; "
                                        "run scripts/gate.py (5.0)")
    record = read_json(workspace / GATE_RECORD)
    if record is None or record.get("mode") not in ("plugin", "manual"):
        raise Invalid("bad_gate_record", f"{GATE_RECORD} is not JSON recording a mode (plugin or manual)")
    if not record.get("finished"):
        raise Invalid("gate_unfinished", f"{GATE_RECORD} records no finished run (stopped, failed or still "
                                         "running); re-run 5.0")
    hashes = ("skill_sha256", "cases_sha256", "run_case_sha256", "inputs_sha256", "artifact_sha256", "split_sha256")
    if not record.get("skill_sha256") or not record.get("cases_sha256") or not all(k in record for k in hashes):
        raise Invalid("gate_unhashed", f"{GATE_RECORD} lacks a skill, case, scorer, input, split or artifact hash "
                                       "(an older gate.py); re-run 5.0")
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
    if cases_sha256(workspace, split.plugin_eval) != record["cases_sha256"]:
        raise Invalid("stale_cases", "gate cases changed since the gate; re-run 5.0 (data-split.yaml "
                                     "plugin_eval or the suite under plugin/evals/)")
    then, now = record["inputs_sha256"] or {}, inputs_sha256(workspace)
    changed = ([RUN_CASE.as_posix()] if file_sha256(workspace / RUN_CASE) != record["run_case_sha256"] else []) \
        + ([f"{DATA_SPLIT.as_posix()} (its splits)"] if split_sha256(split) != record["split_sha256"] else []) \
        + sorted(p for p in then.keys() | now.keys() if then.get(p) != now.get(p))
    if changed:
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        raise Invalid("stale_inputs", f"gate inputs changed since the gate; re-run 5.0 ({shown})")
    return record


def _coverage(recorded: list[str], names: list[str]) -> None:
    if len(set(names)) != len(names):
        raise Invalid("coverage", "artifact lists a case more than once")
    missing, extra = [c for c in recorded if c not in names], [n for n in names if n not in recorded]
    if missing or extra:
        hint = "; a case plugin eval cannot load is skipped — `gate.py record` names it" if missing else ""
        raise Invalid("coverage", f"artifact cases differ from data-split.yaml plugin_eval: "
                                  f"missing {missing or 'none'}, unrecorded {extra or 'none'}{hint}")


def _cases(data: dict, artifact: str) -> list[dict]:
    if data.get("partial") is not False:
        reason = f" ({data['partialReason']})" if data.get("partialReason") else ""
        state = f"partial{reason}" if data.get("partial") is True else "records no \"partial\": false"
        raise Invalid("partial", f"{artifact} is {state}; re-run the gate")
    cases = data.get("cases")
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        raise Invalid("bad_artifact", f"{artifact} has no cases list")
    return cases


def _plugin(data: dict, recorded: list[str]) -> list[CaseResult]:
    """Cases are classed by their graders: only `tool_used` on `Skill` makes a should-trigger indicator (`max: 0`:
    a near miss); anything else is gold and needs a numeric delta. Every indicator must pass in every run that
    counts: a trigger in the with arm, a near miss in both."""
    results = []
    for case in _cases(data, "plugin-eval.json"):
        name, arms = str(case.get("name")), case.get("arms") or {}
        done = {arm: [r for r in arms.get(arm) if isinstance(r, dict) and not r.get("error")]
                if isinstance(arms.get(arm), list) else [] for arm in ("with", "without")}
        for arm, runs in done.items():
            if len(runs) < REQUIRED_RUNS:
                raise Invalid("too_few_runs", f"case {name!r} arm {arm!r} has {len(runs)} completed runs; "
                                              f"{REQUIRED_RUNS} required")
        delta = (case.get("aggregates") or {}).get("delta")
        result = CaseResult(name, float(delta) if is_number(delta) else None)
        graders = [g for g in case.get("graders") or [] if isinstance(g, dict)]
        skill = [g for g in graders if g.get("type") == "tool_used" and (g.get("config") or {}).get("tool") == "Skill"]
        if graders and len(skill) == len(graders):
            result.kind = "near_miss" if any((g.get("config") or {}).get("max") == 0 for g in skill) else "trigger"
        elif result.delta is None:
            raise Invalid("no_delta", f"gold case {name!r} has no numeric with/without delta ({delta!r}); "
                                      "re-run the gate")
        for grader in skill:
            near = (grader.get("config") or {}).get("max") == 0
            runs = done["with"] + done["without"] if near else done["with"]
            passed = sum(1 for run in runs for g in run.get("graders") or []
                         if isinstance(g, dict) and g.get("name") == grader.get("name") and g.get("passed") is True)
            setattr(result, "near_miss" if near else "trigger", f"{passed}/{len(runs)}")
            if passed < len(runs):
                code, what = (("near_miss_fired", f"near miss {name!r} fired the skill in {len(runs) - passed}/"
                               f"{len(runs)} runs (either arm)") if near else
                              ("trigger_failed", f"case {name!r}: the skill did not fire in {len(runs) - passed}/"
                               f"{len(runs)} with-arm runs"))
                raise Invalid(code, f"{what}; a description defect: return to 4.6, then re-run 5.0")
        results.append(result)
    _coverage(recorded, [r.name for r in results])
    return results


def _reused_baseline(workspace: Path, name: str, case: dict, summary: dict) -> None:
    """A without-skill arm the gate took from baseline-cache/ must still match that entry: its key recomputed
    from the inputs now, its file hash as the gate read it, and its scores as the artifact lists them."""
    reused = case["without_cache"]
    key = reused.get("key") if isinstance(reused, dict) else None
    if not isinstance(key, str) or not BASELINE_KEY.fullmatch(key) or not isinstance(reused.get("sha256"), str):
        raise Invalid("stale_baseline", f"case {name!r}: without_cache needs a sha256 key and file hash; re-run 5.0")
    path = baseline_path(workspace, key)
    stale = None
    if file_sha256(path) != reused["sha256"]:
        stale = f"{path.relative_to(workspace).as_posix()} is missing or not the entry the gate reused"
    elif key != baseline_key(workspace, name, summary.get("model"), summary.get("executor")):
        stale = "its key no longer matches the case, model, executor, run-case.sh or gate inputs"
    elif read_baseline(workspace, key, len(case["without"])) != [float(s) for s in case["without"]]:
        stale = "its scores differ from the artifact's without arm"
    if stale:
        raise Invalid("stale_baseline", f"case {name!r}: the cached without-skill arm is stale ({stale}); re-run 5.0 "
                                        "with --fresh-baseline")


def _manual(data: dict, recorded: list[str], workspace: Path) -> list[CaseResult]:
    """Every manual case is gold: its delta is mean(with) - mean(without)."""
    results = []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    for case in _cases(data, "uplift.json"):
        name = str(case.get("case"))
        for arm in ("with", "without"):
            runs = case.get(arm)
            if not isinstance(runs, list) or not all(is_number(s) and 0 <= s <= 1 for s in runs):
                raise Invalid("bad_artifact", f"case {name!r} arm {arm!r} is not a list of scores in 0..1")
            if len(runs) < REQUIRED_RUNS:
                raise Invalid("too_few_runs", f"case {name!r} arm {arm!r} has {len(runs)} runs; {REQUIRED_RUNS} required")
        if "without_cache" in case:
            _reused_baseline(workspace, name, case, summary)
        results.append(CaseResult(name, _mean(case["with"]) - _mean(case["without"])))
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
        if not (workspace / rel).is_file():
            raise Invalid("no_artifact", f"mode {mode}: {rel} not found; run the gate (5.0)")
        if file_sha256(workspace / rel) != record["artifact_sha256"]:
            raise Invalid("stale_inputs", f"gate inputs changed since the gate; re-run 5.0 ({rel} is not the "
                                          "artifact the gate wrote)")
        data = read_json(workspace / rel)
        if data is None:
            raise Invalid("bad_artifact", f"{rel} is not a JSON object")
        results = _plugin(data, split.plugin_eval) if mode == "plugin" else _manual(data, split.plugin_eval, workspace)
        if split.gold_recorded and not any(r.kind == "gold" for r in results):
            raise Invalid("no_gold", f"{DATA_SPLIT} records gold standards but no gate case is a gold case "
                                     "(every case grades only `tool_used` on Skill); 3.6 adds one per gate "
                                     "gold standard")
    except Invalid as exc:
        return Verdict(False, mode, None if rel is None else rel.as_posix(), exc.reason, code=exc.code)
    gold = [r for r in results if r.kind == "gold"]
    return Verdict(True, mode, rel.as_posix(), "ok", cases=len(results), gold_cases=len(gold),
                   mean_delta=_mean([r.delta for r in gold]), non_positive=[r.name for r in gold if r.delta <= 0],
                   triggers={r.name: r.trigger for r in results if r.trigger is not None},
                   near_misses={r.name: r.near_miss for r in results if r.near_miss is not None},
                   skill=record["skill"])


def _spaced(text: str) -> str:
    return " ".join(text.split())


def finish(workspace: Path, skill: str | None = None) -> tuple[bool, str | None, str]:
    """(finished, verdict, reason): BENCHMARK.md states a terminal verdict, no `gate` job is live, and the
    verdict stands on what the gate and the panel allow now, whose current lines BENCHMARK.md carries."""
    bench = workspace / BENCHMARK
    if not bench.is_file():
        return False, None, f"no {BENCHMARK} at the build root; carry Phase 5 through 5.4 to a terminal verdict"
    text = bench.read_text(encoding="utf-8", errors="replace")
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
    unshipped = verdict == "UNSHIPPED"
    if unshipped and not any(a.get("step") == "gate" for a in read_attempts(workspace)):
        # A `gate` step is a gate run that started or a GateError it raised; a failed `gate.py record` is not.
        return False, verdict, (f"UNSHIPPED needs a gate attempt: no gate run or gate-step error is logged in "
                                f"{GATE_ATTEMPTS}; run 3.6 and 5.0 (a build that does not ship still runs the gate)")
    last = panel.exhausted(workspace)
    if last and unshipped and _spaced(last.get("line") or "") not in _spaced(text):
        return False, verdict, (f"the panel ran its {panel.rules()['max_panel_rounds']} rounds; {BENCHMARK} lacks the "
                                f"last round's PANEL CONSENSUS line beside UNSHIPPED; copy it in verbatim: "
                                f"{last.get('line')}")
    gate = validate(workspace, skill)
    if not gate.valid:
        return (True, verdict, "ok") if unshipped else (
            False, verdict, f"{verdict} needs a valid gate: {gate.line()}; return to 5.0")
    if _spaced(gate.line()) not in _spaced(text):
        return False, verdict, (f"{BENCHMARK} lacks check_gate.py's current GATE VALID line beside the {verdict} "
                                f"verdict; copy it in verbatim: {gate.line()}")
    if verdict == "SHIP" and gate.non_positive:
        return False, verdict, (f"gold delta<=0 on {', '.join(gate.non_positive)} caps the verdict at SHIP WITH "
                                "CAVEATS (5.4); write that, with why the case cannot benefit")
    consensus, why = panel.recorded(workspace, gate.skill, skill_sha256(workspace, gate.skill))
    if consensus is None:
        return (True, verdict, "ok") if unshipped else (
            False, verdict, f"{verdict} needs the panel's consensus on this skill: {why}")
    if panel.RANK[verdict] > panel.RANK[consensus.allowed]:
        return False, verdict, (f"the panel's consensus allows at most {consensus.allowed} ({consensus.line()}); "
                                f"write that verdict, or return to Phase 4 and re-run 5.0-5.3")
    if _spaced(consensus.line()) not in _spaced(text):
        return False, verdict, (f"{BENCHMARK} lacks the panel's current PANEL CONSENSUS line beside the {verdict} "
                                f"verdict; copy it in verbatim: {consensus.line()}")
    return True, verdict, "ok"


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _changed_since(workspace: Path, commit: object, skill: str) -> str | None:
    """Why output/<skill>/ cannot be shown unchanged since `commit` (tracked edits or new files), or None."""
    ref = commit.strip() if isinstance(commit, str) else ""
    resolved = _git(workspace, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") if ref else None
    if resolved is None or resolved.returncode != 0:
        return (f"experiment 0 logs no commit git can resolve ({commit!r}); log the baseline commit's hash "
                "(git rev-parse HEAD after the 3.5 commit)")
    diff = _git(workspace, "diff", "--quiet", ref, "--", f"output/{skill}")
    new = _git(workspace, "ls-files", "--others", "--exclude-standard", "--", f"output/{skill}")
    if diff is None or diff.returncode not in (0, 1) or new is None or new.returncode != 0:
        return f"git cannot compare output/{skill}/ with commit {ref}"
    if diff.returncode == 1 or new.stdout.strip():
        return f"output/{skill}/ changed since experiment 0 (commit {ref})"
    return None


def _experiments(workspace: Path) -> tuple[list[dict], list[dict], float]:
    """(every log entry, the experiment lines, the log's mtime) from the first experiment log that exists; a
    `type` other than "experiment" (validation_check, description_pass) is not an experiment."""
    log = next((workspace / p for p in EXPERIMENT_LOGS if (workspace / p).is_file()), None)
    entries = json_lines(log)
    experiments = [e for e in entries if "experiment" in e and e.get("type") in (None, "experiment")]
    return entries, experiments, log.stat().st_mtime if log else 0.0


def fast_path(workspace: Path, skill: str | None = None) -> tuple[bool, str]:
    """(skip the Phase 4 loop, why), decided at the end of 3.5 from what exists then: experiment 0 (the baseline)
    is the only experiment logged and meets target_score, the 4.2 validation check on it passes (a split with a
    validation list), and output/<skill>/ is unchanged since its commit. The 5.0 gate runs either way."""
    try:
        name = resolve_skill(workspace, skill)
        split = read_data_split(workspace / DATA_SPLIT)
        target = panel.read_rubric(workspace).target_score
    except GateError as exc:
        return False, str(exc)
    entries, experiments, _ = _experiments(workspace)
    zero = [e for e in experiments if e.get("experiment") == 0]
    if not zero:
        return False, "no experiment 0 in autoresearch.jsonl (3.5 logs the baseline there)"
    if later := [e for e in experiments if e.get("experiment") != 0]:
        return False, f"Phase 4 already logged {len(later)} experiment(s); the fast path is for the 3.5 baseline"
    metric = zero[-1].get("metric")
    if not is_number(metric):
        return False, f"experiment 0 has no numeric metric ({metric!r})"
    if metric + 1e-9 < target:
        return False, f"baseline overall_score {metric:.3f} is below target_score {target:.2f}"
    validated = ""
    if split.gold.get("validation"):
        checks = [e for e in entries if e.get("type") == "validation_check" and e.get("experiment") == 0]
        score = checks[-1].get("validation_score") if checks else None
        if not is_number(score):
            return False, ("no experiment-0 validation_check with a numeric validation_score in autoresearch.jsonl; "
                           "score the validation list and log it (phase-4.md 4.2)")
        if score + VALIDATION_DROP < metric - 1e-9:
            return False, (f"validation_score {score:.3f} is more than {VALIDATION_DROP} below the baseline "
                           f"{metric:.3f} (phase-4.md 4.2)")
        validated = f", validation_score {score:.3f}"
    if why := _changed_since(workspace, zero[-1].get("commit"), name):
        return False, why
    return True, (f"baseline overall_score {metric:.3f} >= target_score {target:.2f}{validated}; skip the Phase 4 "
                  "loop (4.1-4.6): run 3.6, then Phase 5")


def _gold_non_positive(workspace: Path) -> list[str]:
    """The gold cases the last finished gate scored at delta <= 0, read from its artifact without the hash checks
    (the skill may have changed since): [] when there is no such gate or it cannot be read."""
    record = read_json(workspace / GATE_RECORD)
    if not record or not record.get("finished") or record.get("mode") not in ("plugin", "manual"):
        return []
    try:
        split = read_data_split(workspace / DATA_SPLIT)
        plugin = record["mode"] == "plugin"
        data = read_json(workspace / (PLUGIN_ARTIFACT if plugin else MANUAL_ARTIFACT))
        if data is None or not split.plugin_eval:
            return []
        results = _plugin(data, split.plugin_eval) if plugin else _manual(data, split.plugin_eval, workspace)
    except (GateError, Invalid):
        return []
    return [r.name for r in results if r.kind == "gold" and r.delta is not None and r.delta <= 0]


def _without_description(text: str) -> str:
    """SKILL.md with its frontmatter `description` (and that value's indented continuation lines) removed."""
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0) if lines[:1] == ["---"] else 0
    kept, skipping = [], False
    for i, line in enumerate(lines):
        if 0 < i < end:
            if line.startswith("description:"):
                skipping = True
                continue
            if skipping and line[:1].isspace():
                continue
            skipping = False
        kept.append(line)
    return "\n".join(kept)


def _description_only(workspace: Path, experiments: list[dict], skill: str | None) -> str | None:
    """The kept experiment whose commit output/<skill>/ differs from only in SKILL.md's description (the 4.6
    pass, which re-scores before it keeps a new one), or None."""
    kept = [e for e in experiments if str(e.get("status") or "").lower() not in DISCARDED
            and isinstance(e.get("commit"), str) and e["commit"].strip()]
    if not kept:
        return None
    try:
        name = resolve_skill(workspace, skill)
    except GateError:
        return None
    commit, path = kept[-1]["commit"].strip(), f"output/{name}/SKILL.md"
    diff = _git(workspace, "diff", "--name-only", "--relative", commit, "--", f"output/{name}")
    new = _git(workspace, "ls-files", "--others", "--exclude-standard", "--", f"output/{name}")
    old = _git(workspace, "show", f"{commit}:{path}")
    if any(p is None or p.returncode != 0 for p in (diff, new, old)) or new.stdout.strip() \
            or diff.stdout.split() != [path]:
        return None
    now = (workspace / path).read_text(encoding="utf-8", errors="replace")
    return str(kept[-1]["experiment"]) if _without_description(old.stdout) == _without_description(now) else None


def loop_done(workspace: Path, skill: str | None = None) -> tuple[bool, str]:
    """(the Phase 4 loop is over, why), from the experiment log (phase-4.md 4.3): the 3.5 fast path holds, the best
    kept overall_score is at its maximum (1.0), or the last experiment brought the build to or kept it at
    target_score with no gain above the noise floor (NOISE_FLOOR, the one noise value on disk) over the best kept
    before it. It reopens for one more metric run when the panel's latest consensus is ITERATE or BLOCK and newer
    than the log, when only experiment 0 is logged and a newer gate scored a gold case at delta <= 0 (phase-3.md
    3.5), or when only the description changed since the last kept experiment (4.6)."""
    _, experiments, logged = _experiments(workspace)
    scored = [e for e in experiments if is_number(e.get("metric"))]
    if not scored:
        return False, "no scored experiment logged yet"
    consensus = read_json(workspace / panel.CONSENSUS)
    label = consensus.get("label") if consensus else None
    if label in REOPENED and (workspace / panel.CONSENSUS).stat().st_mtime > logged:
        return False, f"the panel's latest consensus, {label}, is newer than the last logged experiment"
    gate = workspace / GATE_RECORD
    if all(e.get("experiment") == 0 for e in experiments) and gate.is_file() and gate.stat().st_mtime > logged \
            and (names := _gold_non_positive(workspace)):
        return False, (f"the gate scored gold delta<=0 on {', '.join(names)} with only experiment 0 logged "
                       "(phase-3.md 3.5)")
    if fast_path(workspace, skill)[0]:
        done = "experiment 0 meets target_score: the 3.5 fast path, so run 3.6 first"
    else:
        try:
            target = panel.read_rubric(workspace).target_score
        except GateError as exc:
            return False, str(exc)
        kept = [e for e in scored if str(e.get("status") or "").lower() not in DISCARDED]
        best = max((e["metric"] for e in kept), default=None)
        last = scored[-1]
        prior = max((e["metric"] for e in kept if e is not last), default=None)
        if best is not None and best >= 1 - 1e-9:
            done = f"the best overall_score is {best:.3f}, its maximum"
        elif prior is not None and len(scored) > 1 and max(prior, last["metric"]) + 1e-9 >= target \
                and last["metric"] <= prior + NOISE_FLOOR + 1e-9:
            done = (f"experiment {last.get('experiment')} scored {last['metric']:.3f}, no gain above the "
                    f"{NOISE_FLOOR} noise floor over the best {prior:.3f}, at target_score {target:.2f}")
        else:
            return False, "no stop rule holds (fast path, the 1.0 ceiling, at target with no gain)"
    if (experiment := _description_only(workspace, experiments, skill)) is not None:
        return False, f"only the description changed since experiment {experiment}: the 4.6 pass may score it"
    return True, done


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
    step.add_argument("--consensus", action="store_true", help="5.3: the panel's verdict from the three receipts")
    step.add_argument("--finish", action="store_true", help="the build's end state instead of the gate")
    step.add_argument("--fast-path", action="store_true", help="3.5: may the build skip the Phase 4 loop")
    step.add_argument("--loop", action="store_true", help="4.3: is the Phase 4 loop over (the loop guard's check)")
    parser.add_argument("--synthesis", action="store_true", help="with --panel-receipt: the synthesis round (5.3)")
    parser.add_argument("--verbose", action="store_true", help="longer lines where the default is terse")
    args = parser.parse_args(argv)
    if args.synthesis and not args.panel_receipt:
        parser.error("--synthesis goes with --panel-receipt")
    workspace = args.workspace.resolve()
    if args.fast_path:
        skip, reason = fast_path(workspace, args.skill)
        print(json.dumps({"fast_path": skip, "reason": reason}) if args.json else
              (f"FAST PATH: {reason}" if skip else f"NO FAST PATH: {reason}"))
        return 0 if skip else 1
    if args.loop:
        done, reason = loop_done(workspace, args.skill)
        print(json.dumps({"loop_done": done, "reason": reason}) if args.json else
              (f"LOOP DONE: {reason}" if done else f"LOOP OPEN: {reason}"))
        return 0 if done else 1
    if args.finish:
        done, label, reason = finish(workspace, args.skill)
        print(json.dumps({"finished": done, "verdict": label, "reason": reason}) if args.json else
              (f"BUILD FINISHED: verdict {label}" if done else f"BUILD UNFINISHED: {reason}"))
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
            print(f"PANEL RECEIPT: {path.relative_to(workspace).as_posix()} ({role}, round {2 if args.synthesis else 1})"
                  + (f" binds these scores to output/{verdict.skill}/ as it is now" if args.verbose else ""))
        elif args.consensus:
            print(panel.write_consensus(workspace, verdict.skill, skill_sha).line())
        elif args.panel:
            print(f"PANEL ROUND {panel.check_round(workspace, skill_sha)}: each role scores every dimension")
            print(f"panel: after scoring, each verifier runs `check_gate.py --panel-receipt <role> <scores.json>` "
                  f"({', '.join(panel.ROLES)}); then `check_gate.py --consensus` (5.3)", file=sys.stderr)
    except panel.RoundsExhausted as exc:
        print(f"PANEL ROUNDS EXHAUSTED: {exc}")
        return 1
    except GateError as exc:
        print(f"PANEL INCOMPLETE: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
