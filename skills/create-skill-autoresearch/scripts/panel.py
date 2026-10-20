"""Panel role receipts and the consensus they derive (SKILL.md 5.2-5.4), for check_gate.py.

Each verifier writes its role receipt after it scores, with
`check_gate.py --panel-receipt <role> <scores.json>`: the JSON its panel prompt template outputs
(references/pipeline-phases.md: `scores` per rubric dimension, `pass_recommendation`, and for the
Devil's Advocate `escalation`). The receipt, work/experiments/panel/<role>.json, binds those scores to
the sha256 of output/<skill>/ and of work/evaluation/rubric.yaml. A synthesis round's output
(`revised_scores`) goes through `--panel-receipt <role> <file> --synthesis` into <role>.synthesis.json;
it may change only the dimensions the first round disputed.

`check_gate.py --consensus` computes the panel's verdict from the three current receipts, by the
thresholds in references/verdict-rules.json (their one home), and writes
work/experiments/panel/consensus.json with the label it allows BENCHMARK.md to state. check_gate.py
--finish recomputes it and rejects a verdict above that label.

Each consensus is also archived as a panel round, work/experiments/panel/rounds/<n>/: round.json (the
skill and rubric hashes, the label and its line, a sha256 per file of output/<skill>/), a copy of SKILL.md
and the three final receipts. A round is one consensus over a skill hash other than the last round's; a
re-run on the same skill (after synthesis, or a rubric fix) updates that round. Past `max_panel_rounds`
(verdict-rules.json) no new round starts: the build ends UNSHIPPED with the last round's consensus line.

rubric.yaml is read by a small stdlib reader of the references/rubric-templates.md schema:
`target_score:` (default from verdict-rules.json), and a `dimensions:` block list whose items carry
`name:`, `weight:`, `scale:` ("1-10" when absent) and optionally `sections:`, a one-line flow list of
SKILL.md heading prefixes the dimension judges (absent: the whole skill).

`post_panel_recheck` in verdict-rules.json picks how a panel round after a skill change re-scores.
`full`: every role scores every dimension. `scoped`: the script diffs SKILL.md against the last round's
archived copy and each role re-scores only the dimensions whose sections the change touches; the rest
carry the last round's final scores forward, tagged with that round's skill hash, and --consensus
re-checks them against the archive. A change to any other file, to the frontmatter, to a section under
`scoped_full_headings`, of more than `scoped_max_changed_lines` lines, or after a rubric change, re-scores
in full. --panel prints which.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from gate_common import EVAL_DIR, GateError, _flow_items, _hashed, _strip_comment, _unquote, check_skill_name, file_sha256, \
    skill_sha256, write_json

RUBRIC = EVAL_DIR / "rubric.yaml"
PANEL_DIR = PurePosixPath("work") / "experiments" / "panel"
CONSENSUS = PANEL_DIR / "consensus.json"
ROUNDS_DIR = PANEL_DIR / "rounds"
RULES_PATH = Path(__file__).resolve().parent.parent / "references" / "verdict-rules.json"
DA = "devils-advocate"
ROLES = ("verifier-a", "verifier-b", DA)
RANK = {"UNSHIPPED": 0, "SHIP WITH CAVEATS": 1, "SHIP": 2}
_LABEL = re.compile(r"[ _]+")


def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


class RoundsExhausted(GateError):
    """A new panel round past `max_panel_rounds`: the build ends UNSHIPPED on the last round's consensus."""


def label(text: object) -> str | None:
    """`SHIP_WITH_CAVEATS`, `ship with caveats` and the like as one spelling, or None."""
    return _LABEL.sub(" ", text.strip().upper()) if isinstance(text, str) and text.strip() else None


@dataclass
class Dimension:
    name: str
    weight: float
    low: float = 1.0
    high: float = 10.0
    sections: list[str] = field(default_factory=list)


@dataclass
class Rubric:
    dimensions: list[Dimension]
    target_score: float
    sha256: str


def read_rubric(workspace: Path) -> Rubric:
    path = workspace / RUBRIC
    if not path.is_file():
        raise GateError(f"{RUBRIC} not found; 2.3 writes it (schema: references/rubric-templates.md)")
    target: float | None = None
    items: list[dict[str, str]] = []
    in_dims = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if not line[0].isspace():
            key, _, value = line.partition(":")
            in_dims = key.strip() == "dimensions"
            if key.strip() == "target_score":
                try:
                    target = float(_unquote(value))
                except ValueError as exc:
                    raise GateError(f"{RUBRIC}: target_score {value.strip()!r} is not a number") from exc
            continue
        if not in_dims:
            continue
        item = line.strip()
        if item.startswith("- "):
            items.append({})
            item = item[2:]
        key, sep, value = item.partition(":")
        if sep and items:
            items[-1][key.strip()] = value.strip() if key.strip() == "sections" else _unquote(value)
    dims = []
    for item in items:
        name = item.get("name")
        try:
            weight = float(item.get("weight", ""))
        except ValueError as exc:
            raise GateError(f"{RUBRIC}: dimension {name!r} has no numeric weight") from exc
        scale = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", item.get("scale", "1-10"))
        if not name or not scale:
            raise GateError(f"{RUBRIC}: each dimension needs a name and a scale like \"1-10\" ({item})")
        sections = item.get("sections", "")
        listed = _flow_items(sections[1:-1]) if sections.startswith("[") and sections.endswith("]") else None
        if sections and (listed is None or not all(e.strip() for e in listed)):
            raise GateError(f"{RUBRIC}: dimension {name!r} sections must be a one-line flow list of SKILL.md "
                            f"headings, like [Workflow, Examples] ({sections})")
        dims.append(Dimension(name, weight, float(scale.group(1)), float(scale.group(2)),
                              [e.strip() for e in listed or []]))
    if not dims or len({d.name for d in dims}) != len(dims):
        raise GateError(f"{RUBRIC}: needs a `dimensions:` block list of uniquely named dimensions")
    if abs(sum(d.weight for d in dims) - 1) > 0.01:
        raise GateError(f"{RUBRIC}: dimension weights sum to {sum(d.weight for d in dims):g}, not 1.0")
    return Rubric(dims, rules()["default_target_score"] if target is None else target, file_sha256(path))


def _receipt_path(workspace: Path, role: str, synthesis: bool) -> Path:
    return workspace / PANEL_DIR / f"{role}{'.synthesis' if synthesis else ''}.json"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_score(name: str, score: object, quote: object, d: Dimension, what: str = "score") -> None:
    """The rubric scale, and evidence for a score at either extreme (verdict-rules.json)."""
    evidence = rules()["evidence_required"]
    if not _number(score) or not d.low <= score <= d.high:
        raise GateError(f"{name}: {what} {score!r} is not on the rubric scale {d.low:g}-{d.high:g}")
    needs = score >= evidence["score_at_least"] or score <= evidence["score_at_most"]
    if needs and not (isinstance(quote, str) and quote.strip()):
        raise GateError(f"{name}: a {what} of {score:g} needs evidence (a verbatim quote or the failing line)")


def _scores(raw: dict, rubric: Rubric, scope: "Scope | None" = None) -> dict[str, dict]:
    dims = {d.name: d for d in rubric.dimensions if scope is None or d.name in scope.rescored}
    if not isinstance(raw, dict) or set(raw) != set(dims):
        got = sorted(raw) if isinstance(raw, dict) else raw
        if scope:
            raise GateError(f"scoped re-check (post_panel_recheck: scoped): score exactly {sorted(dims)}; "
                            f"{', '.join(scope.carried)} carry forward from round {scope.from_round}; got {got}")
        raise GateError(f"scores must cover exactly the rubric's dimensions {sorted(dims)}; got {got}")
    out = {}
    for name, entry in raw.items():
        score = entry.get("score") if isinstance(entry, dict) else None
        conf = entry.get("confidence") if isinstance(entry, dict) else None
        quote = entry.get("evidence") if isinstance(entry, dict) else None
        _check_score(name, score, quote, dims[name])
        if not _number(conf) or not 0 <= conf <= 1:
            raise GateError(f"{name}: confidence {conf!r} is not in 0..1")
        out[name] = {"score": score, "confidence": conf, "evidence": quote if isinstance(quote, str) else ""}
    return out


def _synthesis_scores(raw: dict, first: dict, disputed: list[str], rubric: Rubric) -> dict[str, dict]:
    """A revised score meets the first round's rules: on the scale, and at an extreme backed by evidence,
    its own `evidence`, else the first round's, else its `justification`."""
    dims = {d.name: d for d in rubric.dimensions}
    revised = raw.get("revised_scores")
    if not isinstance(revised, dict):
        raise GateError("a synthesis receipt needs `revised_scores` (the synthesis prompt's output)")
    outside = sorted(set(revised) - set(disputed))
    if outside:
        raise GateError(f"synthesis may change only the disputed dimensions {disputed}; got {outside}")
    out = json.loads(json.dumps(first["scores"]))
    for name, entry in revised.items():
        entry = entry if isinstance(entry, dict) else {}
        score, why = entry.get("revised"), entry.get("justification")
        quote = next((q for q in (entry.get("evidence"), out[name].get("evidence"), why)
                      if isinstance(q, str) and q.strip()), "")
        _check_score(name, score, quote, dims[name], "revised score")
        out[name] = {**out[name], "score": score, "evidence": quote,
                     "justification": why if isinstance(why, str) else ""}
    return out


def write_receipt(workspace: Path, role: str, source: Path, skill: str, skill_sha: str,
                  synthesis: bool = False) -> Path:
    """Validate a verifier's scoring JSON against the rubric and write its role receipt."""
    if role not in ROLES:
        raise GateError(f"role {role!r} is not one of {', '.join(ROLES)}")
    if not synthesis:
        check_round(workspace, skill_sha)
    rubric = read_rubric(workspace)
    raw = _read(source)
    if raw is None:
        raise GateError(f"{source} is not a JSON object (the panel template's output)")
    receipt = {"role": role, "skill": skill, "skill_sha256": skill_sha, "rubric_sha256": rubric.sha256,
               "round": 2 if synthesis else 1}
    if synthesis:
        current = current_receipts(workspace, skill_sha, rubric.sha256)
        missing = [r for r in ROLES if r not in current]
        if missing:
            raise GateError(f"a synthesis receipt needs every first-round receipt for this skill; missing {missing}")
        disputed = disputes(current, rubric)
        if not disputed:
            raise GateError("the first round disputed nothing; there is no synthesis round")
        receipt["scores"] = _synthesis_scores(raw, current[role], disputed, rubric)
        recommendation = raw.get("final_recommendation") or current[role]["recommendation"]
        escalate = current[role].get("escalate") if "escalation" not in raw else raw.get("escalation")
    else:
        scope, _ = recheck_scope(workspace, skill, skill_sha, rubric)
        receipt["scores"] = _scores(raw.get("scores"), rubric, scope)
        if scope:
            merged = {**receipt["scores"], **_carried(workspace, role, scope)}
            receipt["scores"] = {d.name: merged[d.name] for d in rubric.dimensions}
            receipt["recheck"] = scope.summary()
        recommendation, escalate = raw.get("pass_recommendation"), raw.get("escalation")
    receipt["recommendation"] = label(recommendation)
    receipt["escalate"] = escalate if isinstance(escalate, str) and escalate.strip() else None
    receipt["written"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = _receipt_path(workspace, role, synthesis)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not synthesis:
        _receipt_path(workspace, role, True).unlink(missing_ok=True)
    write_json(path, receipt)
    return path


def current_receipts(workspace: Path, skill_sha: str, rubric_sha: str, synthesis: bool = False) -> dict[str, dict]:
    found = {}
    for role in ROLES:
        data = _read(_receipt_path(workspace, role, synthesis))
        if data and data.get("skill_sha256") == skill_sha and data.get("rubric_sha256") == rubric_sha:
            found[role] = data
    return found


def rounds(workspace: Path) -> list[dict]:
    """The archived panel rounds, oldest first; round n lives in rounds/<n>/."""
    found, n = [], 1
    while (entry := _read(workspace / ROUNDS_DIR / str(n) / "round.json")) is not None:
        found.append(entry)
        n += 1
    return found


def panel_round(workspace: Path, skill_sha: str) -> int:
    """The round a consensus on this skill belongs to: the last round when the skill is unchanged since,
    else the next one."""
    archive = rounds(workspace)
    if archive and archive[-1].get("skill_sha256") == skill_sha:
        return len(archive)
    return len(archive) + 1


def check_round(workspace: Path, skill_sha: str) -> int:
    """The round number, or RoundsExhausted when a new round would pass `max_panel_rounds`."""
    n, cap = panel_round(workspace, skill_sha), rules()["max_panel_rounds"]
    if n > cap:
        raise RoundsExhausted(f"the panel ran its {cap} rounds (max_panel_rounds) and the skill changed since round "
                              f"{n - 1}; no round {n} starts. End the build UNSHIPPED: write `Verdict: UNSHIPPED` "
                              f"in BENCHMARK.md beside the last round's line: {rounds(workspace)[-1].get('line')}")
    return n


def exhausted(workspace: Path) -> dict | None:
    """The last round when the panel ran all `max_panel_rounds` and the skill changed since, else None."""
    archive = rounds(workspace)
    if len(archive) < rules()["max_panel_rounds"]:
        return None
    last = archive[-1]
    try:
        current = skill_sha256(workspace, check_skill_name(last.get("skill")))
    except GateError:
        return last
    return None if current == last.get("skill_sha256") else last


def _skill_files(workspace: Path, skill: str) -> dict[str, str]:
    root = workspace / "output" / skill
    return {p.relative_to(root).as_posix(): file_sha256(p)
            for p in sorted(root.rglob("*")) if _hashed(p, root)}


def _archive(workspace: Path, skill: str, skill_sha: str, rubric_sha: str, result: "Consensus") -> None:
    """Write (or, on the same skill, rewrite) the round's record, SKILL.md copy and final receipts."""
    folder = workspace / ROUNDS_DIR / str(result.panel_round)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    shutil.copyfile(workspace / "output" / skill / "SKILL.md", folder / "SKILL.md")
    for role, receipt in current_receipts(workspace, skill_sha, rubric_sha, synthesis=bool(result.disputed)).items():
        write_json(folder / f"{role}.json", receipt)
    write_json(folder / "round.json", {
        "round": result.panel_round, "skill": skill, "skill_sha256": skill_sha, "rubric_sha256": rubric_sha,
        "label": result.label, "allowed": result.allowed, "line": result.line(), "recheck": result.recheck,
        "files": _skill_files(workspace, skill),
        "written": datetime.now(timezone.utc).isoformat(timespec="seconds")})


_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


@dataclass
class Scope:
    """A scoped re-check (post_panel_recheck: scoped): each role re-scores `rescored`; `carried` keep the
    final scores of round `from_round`, on skill `from_skill_sha256`."""
    rescored: list[str]
    carried: list[str]
    from_round: int
    from_skill_sha256: str

    def summary(self) -> dict:
        return {"mode": "scoped", **asdict(self)}


def _sections(lines: list[str]) -> tuple[list[tuple[str, ...]], int]:
    """(the heading chain each line sits under, the index of the frontmatter's closing `---` or -1)."""
    front = -1
    if lines and lines[0].strip() == "---":
        front = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines) - 1)
    chains, stack, fenced = [], [], False
    for i, line in enumerate(lines):
        if i > front and _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced or i <= front else _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            stack = [(lvl, text) for lvl, text in stack if lvl < level] + [(level, heading.group(2))]
        chains.append(tuple(text for _, text in stack))
    return chains, front


def _changed(old: list[str], new: list[str]) -> tuple[int, list[tuple[str, ...]], bool]:
    """(changed line count, the heading chains of the changed lines, whether the frontmatter changed)."""
    old_chains, old_front = _sections(old)
    new_chains, new_front = _sections(new)
    count, chains, front = 0, [], False
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        count += (i2 - i1) + (j2 - j1)
        chains += old_chains[i1:i2] + new_chains[j1:j2]
        front = front or any(i <= old_front for i in range(i1, max(i2, i1 + 1))) \
            or any(j <= new_front for j in range(j1, max(j2, j1 + 1)))
        if i1 == i2:  # a pure insertion also sits in the old section it lands in
            chains.append(old_chains[i1 - 1] if 0 < i1 <= len(old_chains) else ())
    return count, chains, front


def _matches(chain: tuple[str, ...], entries: list[str], anywhere: bool = False) -> bool:
    """A heading in the chain starts with an entry (a rubric `sections` prefix), or with `anywhere`
    contains it (scoped_full_headings), case-insensitively."""
    def hit(heading: str, entry: str) -> bool:
        return entry in heading if anywhere else heading.startswith(entry)
    return any(hit(h.strip().lower(), e.strip().lower()) for h in chain for e in entries if e.strip())


def recheck_scope(workspace: Path, skill: str, skill_sha: str, rubric: Rubric) -> tuple[Scope | None, str]:
    """(the scoped re-check this panel round allows, why) or (None, why the round re-scores in full)."""
    r = rules()
    if r.get("post_panel_recheck", "full") != "scoped":
        return None, "post_panel_recheck is full"
    archive = rounds(workspace)
    if archive and archive[-1].get("skill_sha256") == skill_sha:
        archive = archive[:-1]
    if not archive:
        return None, "the first panel round"
    prior, n = archive[-1], len(archive)
    if prior.get("rubric_sha256") != rubric.sha256:
        return None, f"the rubric changed since round {n}"
    folder = workspace / ROUNDS_DIR / str(n)
    then, now = prior.get("files") or {}, _skill_files(workspace, skill)
    changed = sorted(p for p in then.keys() | now.keys() if then.get(p) != now.get(p))
    if any(p != "SKILL.md" for p in changed):
        return None, f"files other than SKILL.md changed since round {n} ({', '.join(p for p in changed if p != 'SKILL.md')})"
    if file_sha256(folder / "SKILL.md") != then.get("SKILL.md"):
        return None, f"round {n}'s archived SKILL.md does not match its record"
    old = (folder / "SKILL.md").read_text(encoding="utf-8").splitlines()
    new = (workspace / "output" / skill / "SKILL.md").read_text(encoding="utf-8").splitlines()
    count, chains, front = _changed(old, new)
    if count > r["scoped_max_changed_lines"]:
        return None, f"{count} changed lines since round {n} (scoped_max_changed_lines {r['scoped_max_changed_lines']})"
    if front:
        return None, "the frontmatter changed"
    if any(_matches(c, r["scoped_full_headings"], anywhere=True) for c in chains):
        return None, f"the change touches a section under {r['scoped_full_headings']} (scoped_full_headings)"
    for role in ROLES:
        data = _read(folder / f"{role}.json")
        if not data or data.get("skill_sha256") != prior.get("skill_sha256"):
            return None, f"round {n} archived no receipt from {role} for its skill"
    rescored = [d.name for d in rubric.dimensions if not d.sections or any(_matches(c, d.sections) for c in chains)]
    if not rescored:
        return None, "the change touches no section a dimension maps to"
    if len(rescored) == len(rubric.dimensions):
        return None, "the change touches every dimension"
    return Scope(rescored, [d.name for d in rubric.dimensions if d.name not in rescored], n,
                 prior["skill_sha256"]), "ok"


def _carried(workspace: Path, role: str, scope: Scope) -> dict:
    """The final scores round `scope.from_round` archived for `role`, for the carried dimensions."""
    data = _read(workspace / ROUNDS_DIR / str(scope.from_round) / f"{role}.json") or {}
    if data.get("skill_sha256") != scope.from_skill_sha256:
        raise GateError(f"round {scope.from_round}'s archived {role} receipt is not for skill "
                        f"{scope.from_skill_sha256[:12]}; carry-forward refused")
    return {name: {**data["scores"][name], "carried_from_sha256": scope.from_skill_sha256} for name in scope.carried}


def check_scope(workspace: Path, skill: str, skill_sha: str, rubric: Rubric, receipts: dict[str, dict]) -> Scope | None:
    """Every first-round receipt was written under the re-check scope that holds now, and its carried
    scores are the archived round's, bound to that round's skill hash."""
    scope, why = recheck_scope(workspace, skill, skill_sha, rubric)
    expected = scope.summary() if scope else None
    for role, receipt in receipts.items():
        if receipt.get("recheck") != expected:
            raise GateError(f"{role}'s receipt was written under another re-check scope than now "
                            f"({'scoped: ' + ', '.join(scope.rescored) if scope else 'full: ' + why}); re-submit it")
        if scope:
            carried = _carried(workspace, role, scope)
            if any(receipt["scores"].get(name) != carried[name] for name in scope.carried):
                raise GateError(f"{role}'s carried scores differ from round {scope.from_round}'s archived "
                                f"receipt; re-submit it")
    return scope


def _overall(scores: dict[str, float], rubric: Rubric) -> float:
    return sum(scores[d.name] / d.high * d.weight for d in rubric.dimensions)


def disputes(receipts: dict[str, dict], rubric: Rubric) -> list[str]:
    """The dimensions a first round sends to synthesis (consensus-protocol.md, Disagreement Detection)."""
    trig, target = rules()["synthesis_triggers"], rubric.target_score
    split = False
    overalls = [_overall({n: s["score"] for n, s in r["scores"].items()}, rubric) for r in receipts.values()]
    if overalls and max(overalls) >= target and min(overalls) < target - trig["pass_fail_margin"]:
        split = True
    carried = {name for r in receipts.values() for name, s in r["scores"].items() if "carried_from_sha256" in s}
    out = []
    for d in rubric.dimensions:
        if d.name in carried:  # settled in the round it was scored in
            continue
        scores = [r["scores"][d.name]["score"] for r in receipts.values()]
        confs = [r["scores"][d.name]["confidence"] for r in receipts.values()]
        spread = max(scores) - min(scores)
        if (spread >= trig["spread_at_least"]
                or receipts[DA]["scores"][d.name]["score"] <= trig["devils_advocate_at"]
                or (min(confs) < trig["low_confidence_below"] and max(confs) > trig["high_confidence_above"])
                or (split and spread > 0)):
            out.append(d.name)
    return out


@dataclass
class Consensus:
    label: str
    allowed: str
    overall: float | None
    target: float
    dimensions: dict[str, float | None] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)
    deadlocked: list[str] = field(default_factory=list)
    dissent: dict[str, str] = field(default_factory=dict)
    disputed: list[str] = field(default_factory=list)
    escalate: str | None = None
    rounds: int = 1
    recheck: dict = field(default_factory=lambda: {"mode": "full"})
    panel_round: int = 1
    max_panel_rounds: int = 3

    def line(self) -> str:
        overall = "n/a" if self.overall is None else f"{self.overall:.3f}"
        return (f"PANEL CONSENSUS: {self.label} (allows {self.allowed}); overall={overall} "
                f"target={self.target:.2f}; blocked: {', '.join(self.blocked) or 'none'}; "
                f"deadlocked: {', '.join(self.deadlocked) or 'none'}; "
                f"escalate: {'yes' if self.escalate else 'no'}")


def _resolve(scores: list[float], within: float) -> tuple[float | None, str]:
    """(consensus score, how): all within `within` -> their mean; else the closest agreeing pair
    (the lower pair on a tie, the conservative reading) -> its mean; else deadlock (None)."""
    if max(scores) - min(scores) <= within:
        return sum(scores) / len(scores), "converged"
    pairs = sorted((abs(a - b), (a + b) / 2) for i, a in enumerate(scores) for b in scores[i + 1:]
                   if abs(a - b) <= within)
    return (pairs[0][1], "majority") if pairs else (None, "deadlock")


def compute(workspace: Path, skill: str, skill_sha: str) -> Consensus:
    """The consensus of the three current receipts; raises GateError naming what is missing, including
    a synthesis round the first round calls for, and RoundsExhausted past `max_panel_rounds`."""
    rubric, r = read_rubric(workspace), rules()
    panel_n = check_round(workspace, skill_sha)
    first = current_receipts(workspace, skill_sha, rubric.sha256)
    missing = [role for role in ROLES if role not in first]
    if missing:
        raise GateError(f"no current role receipt from {', '.join(missing)} for this skill and rubric: each "
                        "verifier runs `check_gate.py --panel-receipt <role> <scores.json>` after scoring (5.2)")
    scope = check_scope(workspace, skill, skill_sha, rubric, first)
    disputed = disputes(first, rubric)
    final = first
    if disputed:
        second = current_receipts(workspace, skill_sha, rubric.sha256, synthesis=True)
        waiting = [role for role in ROLES if role not in second]
        if waiting:
            raise GateError(f"SYNTHESIS NEEDED on {', '.join(disputed)}: run one synthesis round (5.3); "
                            f"{', '.join(waiting)} still to submit `--panel-receipt <role> <file> --synthesis`")
        final = second
    result = Consensus(label="", allowed="", overall=None, target=rubric.target_score, disputed=disputed,
                       rounds=2 if disputed else 1, panel_round=panel_n,
                       recheck=scope.summary() if scope else {"mode": "full"}, max_panel_rounds=r["max_panel_rounds"])
    for d in rubric.dimensions:
        scores = [final[role]["scores"][d.name]["score"] for role in ROLES]
        value, how = _resolve(scores, r["agree_within"])
        result.dimensions[d.name] = None if value is None else round(value, 4)
        if how == "deadlock":
            result.deadlocked.append(d.name)
        elif how == "majority":
            result.dissent[d.name] = ", ".join(f"{role} {s:g}" for role, s in zip(ROLES, scores))
        if sum(1 for s in scores if s < r["block_below"]) >= r["block_majority"]:
            result.blocked.append(d.name)
    result.escalate = final[DA].get("escalate")
    if not result.deadlocked:
        result.overall = round(_overall(result.dimensions, rubric), 4)
    if result.deadlocked or result.escalate:
        result.label = "ESCALATE"
    elif result.blocked:
        result.label = "BLOCK"
    elif result.overall + 1e-9 >= rubric.target_score:
        result.label = "SHIP"
    elif result.overall + 1e-9 >= rubric.target_score - r["caveat_margin"]:
        result.label = "SHIP WITH CAVEATS"
    else:
        result.label = "ITERATE"
    result.allowed = r["allowed_terminal"][result.label]
    return result


def receipts_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for role in ROLES:
        for synthesis in (False, True):
            path = _receipt_path(workspace, role, synthesis)
            digest.update(f"{role}{synthesis}".encode() + (file_sha256(path) or "-").encode())
    return digest.hexdigest()


def write_consensus(workspace: Path, skill: str, skill_sha: str) -> tuple[Path, Consensus]:
    result = compute(workspace, skill, skill_sha)
    rubric_sha = read_rubric(workspace).sha256
    path = workspace / CONSENSUS
    write_json(path, {"skill": skill, "skill_sha256": skill_sha, "rubric_sha256": rubric_sha,
                      "receipts_sha256": receipts_digest(workspace), "line": result.line(),
                      **asdict(result),
                      "written": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    _archive(workspace, skill, skill_sha, rubric_sha, result)
    return path, result


def recorded(workspace: Path, skill: str, skill_sha: str) -> tuple[Consensus | None, str]:
    """(the consensus, "ok") when consensus.json matches the current skill, rubric and receipts and a fresh
    computation; else (None, why)."""
    try:
        result = compute(workspace, skill, skill_sha)
        rubric_sha = read_rubric(workspace).sha256
    except GateError as exc:
        return None, str(exc)
    data = _read(workspace / CONSENSUS)
    if data is None:
        return None, f"no {CONSENSUS.as_posix()}: run `check_gate.py --consensus` (5.3)"
    if (data.get("skill_sha256") != skill_sha or data.get("rubric_sha256") != rubric_sha
            or data.get("receipts_sha256") != receipts_digest(workspace) or data.get("line") != result.line()):
        return None, (f"{CONSENSUS.as_posix()} is stale (the skill, rubric or a receipt changed since); re-run "
                      "`check_gate.py --consensus`")
    return result, "ok"
