"""Panel role receipts, rounds and the consensus they derive (SKILL.md 5.2-5.4), for check_gate.py.

A receipt (work/experiments/panel/<role>.json, <role>.synthesis.json) binds a verifier's scores to the skill and
rubric hashes, once every quote its evidence gives is found verbatim in output/<skill>/ or input/ (_verify_quote); each consensus is archived as a round under work/experiments/panel/rounds/<n>/ with a copy of
SKILL.md and the final receipts. The thresholds and max_panel_rounds live in references/verdict-rules.json;
the protocol in references/consensus-protocol.md; rubric.yaml's schema (read here by a small stdlib reader:
target_score, plateau_window, metric, and dimensions with name, weight, scale) in references/rubric-templates.md.
With `metric: answer-key` the panel is a review: its consensus allows UNSHIPPED only on a veto (a hard defect a
receipt cites), and check_gate.py decides the rest from the gate and the Phase 4 metric (verdict-rules.json
metric_first).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from gate_common import EVAL_DIR, GateError, _strip_comment, _unquote, check_skill_name, \
    file_sha256, is_number, now, read_json, skill_sha256, write_json

RUBRIC = EVAL_DIR / "rubric.yaml"
PANEL_DIR = PurePosixPath("work") / "experiments" / "panel"
CONSENSUS = PANEL_DIR / "consensus.json"
ROUNDS_DIR = PANEL_DIR / "rounds"
RULES_PATH = Path(__file__).resolve().parent.parent / "references" / "verdict-rules.json"
DA = "devils-advocate"
ROLES = ("verifier-a", "verifier-b", DA)
RANK = {"UNSHIPPED": 0, "SHIP WITH CAVEATS": 1, "SHIP": 2}
METRICS = {"answer-key": True, "llm-judge": False}  # rubric.yaml `metric:` -> checkable outputs (metric-first)
QUOTED = re.compile(r'"([^"]+)"|\u201c([^\u201d]+)\u201d')  # "…" or “…”


def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def metric_first() -> dict:
    return rules()["metric_first"]


class RoundsExhausted(GateError):
    """A new panel round past `max_panel_rounds`: the build ends UNSHIPPED on the last round's consensus."""


@dataclass
class Dimension:
    name: str
    weight: float
    low: float = 1.0
    high: float = 10.0


@dataclass
class Rubric:
    dimensions: list[Dimension]
    target_score: float
    sha256: str
    plateau_window: int = 2
    checkable: bool = False  # `metric: answer-key`: the gate and the Phase 4 metric decide the verdict


def read_rubric(workspace: Path) -> Rubric:
    path = workspace / RUBRIC
    if not path.is_file():
        raise GateError(f"{RUBRIC} not found; 2.3 writes it (schema: references/rubric-templates.md)")
    target: float | None = None
    window = 2
    checkable = False
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
            elif key.strip() == "plateau_window":
                if not re.fullmatch(r"[1-9]\d*", _unquote(value)):
                    raise GateError(f"{RUBRIC}: plateau_window {value.strip()!r} is not a whole number >= 1")
                window = int(_unquote(value))
            elif key.strip() == "metric":
                if _unquote(value) not in METRICS:
                    raise GateError(f"{RUBRIC}: metric {value.strip()!r} is not one of {', '.join(METRICS)}")
                checkable = METRICS[_unquote(value)]
        elif in_dims:
            item = line.strip()
            if item.startswith("- "):
                items.append({})
                item = item[2:]
            key, sep, value = item.partition(":")
            if sep and items:
                items[-1][key.strip()] = _unquote(value)
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
        dims.append(Dimension(name, weight, float(scale.group(1)), float(scale.group(2))))
    if not dims or len({d.name for d in dims}) != len(dims):
        raise GateError(f"{RUBRIC}: needs a `dimensions:` block list of uniquely named dimensions")
    if abs(sum(d.weight for d in dims) - 1) > 0.01:
        raise GateError(f"{RUBRIC}: dimension weights sum to {sum(d.weight for d in dims):g}, not 1.0")
    return Rubric(dims, rules()["default_target_score"] if target is None else target, file_sha256(path), window,
                  checkable)


def checkable(workspace: Path) -> bool:
    """rubric.yaml says `metric: answer-key`; False when it is missing or unreadable (the panel-verdict path)."""
    try:
        return read_rubric(workspace).checkable
    except GateError:
        return False


def max_rounds(workspace: Path) -> int:
    """max_panel_rounds for this build: metric_first's when its outputs are checkable, else the panel path's."""
    return metric_first()["max_panel_rounds"] if checkable(workspace) else rules()["max_panel_rounds"]


def _receipt_path(workspace: Path, role: str, synthesis: bool) -> Path:
    return workspace / PANEL_DIR / f"{role}{'.synthesis' if synthesis else ''}.json"


def _check_score(name: str, score: object, quote: object, d: Dimension, what: str = "score") -> None:
    """The rubric scale, and evidence for a score at either extreme (verdict-rules.json)."""
    evidence = rules()["evidence_required"]
    if not is_number(score) or not d.low <= score <= d.high:
        raise GateError(f"{name}: {what} {score!r} is not on the rubric scale {d.low:g}-{d.high:g}")
    if (score >= evidence["score_at_least"] or score <= evidence["score_at_most"]) and not (
            isinstance(quote, str) and quote.strip()):
        raise GateError(f"{name}: a {what} of {score:g} needs evidence (a verbatim quote or the failing line)")


def _spaced(text: str) -> str:
    return " ".join(text.split())


def quotable(workspace: Path, skill: str) -> list[str]:
    """The text files a quote may come from, whitespace-normalised: the skill the receipt binds (output/<skill>/)
    and the gold standards with the rest of input/. Files that are not UTF-8 text are skipped."""
    texts = []
    for root in (workspace / "output" / skill, workspace / "input"):
        for path in sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []:
            try:
                texts.append(_spaced(path.read_bytes().decode("utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
    return texts


def _verify_quote(name: str, evidence: str, texts: list[str]) -> None:
    """Each quoted segment of `evidence` ("…" or “…”; the whole string when it quotes nothing) must occur in one of
    `texts`, whitespace aside: an invented quote fails the receipt."""
    for quote in [_spaced(a or b) for a, b in QUOTED.findall(evidence)] or [_spaced(evidence)]:
        if quote and not any(quote in text for text in texts):
            raise GateError(f"{name}: the evidence quote {quote!r} is not in output/<skill>/ or input/ (verbatim, "
                            "whitespace aside); quote the skill or a gold standard as it reads, in double quotes")


def _extreme(score: float) -> bool:
    evidence = rules()["evidence_required"]
    return score >= evidence["score_at_least"] or score <= evidence["score_at_most"]


def _scores(raw: object, rubric: Rubric, texts: list[str]) -> dict[str, dict]:
    dims = {d.name: d for d in rubric.dimensions}
    if not isinstance(raw, dict) or set(raw) != set(dims):
        got = sorted(raw) if isinstance(raw, dict) else raw
        raise GateError(f"scores must cover exactly the rubric's dimensions {sorted(dims)}; got {got}")
    out = {}
    for name, entry in raw.items():
        entry = entry if isinstance(entry, dict) else {}
        score, conf, quote = entry.get("score"), entry.get("confidence"), entry.get("evidence")
        _check_score(name, score, quote, dims[name])
        if isinstance(quote, str) and quote.strip():
            _verify_quote(name, quote, texts)
        if not is_number(conf) or not 0 <= conf <= 1:
            raise GateError(f"{name}: confidence {conf!r} is not in 0..1")
        out[name] = {"score": score, "confidence": conf, "evidence": quote if isinstance(quote, str) else ""}
    return out


def _synthesis_scores(raw: dict, first: dict, disputed: list[str], rubric: Rubric,
                      texts: list[str]) -> dict[str, dict]:
    """A revised score meets the first round's rules, its evidence being its own `evidence`, else the first
    round's (already verified), else its `justification`; whichever new text backs it is verified like a quote."""
    dims = {d.name: d for d in rubric.dimensions}
    revised = raw.get("revised_scores")
    if not isinstance(revised, dict):
        raise GateError("a synthesis receipt needs `revised_scores` (the synthesis prompt's output)")
    if outside := sorted(set(revised) - set(disputed)):
        raise GateError(f"synthesis may change only the disputed dimensions {disputed}; got {outside}")
    out = json.loads(json.dumps(first["scores"]))
    for name, entry in revised.items():
        entry = entry if isinstance(entry, dict) else {}
        score, why = entry.get("revised"), entry.get("justification")
        quote = next((q for q in (entry.get("evidence"), out[name].get("evidence"), why)
                      if isinstance(q, str) and q.strip()), "")
        _check_score(name, score, quote, dims[name], "revised score")
        own = entry.get("evidence") if isinstance(entry.get("evidence"), str) and entry["evidence"].strip() else None
        if own or (quote and quote == why and _extreme(score)):
            _verify_quote(name, own or quote, texts)
        out[name] = {**out[name], "score": score, "evidence": quote,
                     "justification": why if isinstance(why, str) else ""}
    return out


def _veto(raw: object, role: str, escalate: object, texts: list[str]) -> dict | None:
    """A receipt's `veto` (null, or {kind, evidence, repro}): a hard defect that forces UNSHIPPED on a checkable
    skill. Its evidence quote is verified like a score's; `repro` is the input or command that shows the failure."""
    if raw is None:
        return None
    kinds = metric_first()["veto_kinds"]
    if not isinstance(raw, dict) or raw.get("kind") not in kinds:
        raise GateError(f"veto must be null or {{\"kind\": {' | '.join(kinds)}, \"evidence\", \"repro\"}}; got {raw!r}")
    evidence, repro = raw.get("evidence"), raw.get("repro")
    if not (isinstance(evidence, str) and evidence.strip()) or not (isinstance(repro, str) and repro.strip()):
        raise GateError(f"a {raw['kind']} veto needs `evidence` (a verbatim quote) and `repro` (the input or command "
                        "that reproduces the failure)")
    if raw["kind"] == "escalate" and (role != DA or not (isinstance(escalate, str) and escalate.strip())):
        raise GateError("an escalate veto is the Devil's Advocate's, beside its `escalation`")
    _verify_quote("veto", evidence, texts)
    return {"kind": raw["kind"], "evidence": evidence, "repro": repro}


def write_receipt(workspace: Path, role: str, source: Path, skill: str, skill_sha: str,
                  synthesis: bool = False) -> Path:
    """Validate a verifier's scoring JSON against the rubric and write its role receipt."""
    if role not in ROLES:
        raise GateError(f"role {role!r} is not one of {', '.join(ROLES)}")
    if not synthesis:
        check_round(workspace, skill_sha)
    rubric, texts = read_rubric(workspace), quotable(workspace, skill)
    raw = read_json(source)
    if raw is None:
        raise GateError(f"{source} is not a JSON object (the panel template's output)")
    receipt = {"role": role, "skill": skill, "skill_sha256": skill_sha, "rubric_sha256": rubric.sha256,
               "round": 2 if synthesis else 1}
    if synthesis:
        current = current_receipts(workspace, skill_sha, rubric.sha256)
        if missing := [r for r in ROLES if r not in current]:
            raise GateError(f"a synthesis receipt needs every first-round receipt for this skill; missing {missing}")
        if not (disputed := disputes(current, rubric)):
            raise GateError("the first round disputed nothing; there is no synthesis round")
        receipt["scores"] = _synthesis_scores(raw, current[role], disputed, rubric, texts)
        recommendation = raw.get("final_recommendation") or current[role]["recommendation"]
        escalate = current[role].get("escalate") if "escalation" not in raw else raw.get("escalation")
        veto = _veto(raw["veto"], role, escalate, texts) if "veto" in raw else current[role].get("veto")
        if veto and veto["kind"] == "escalate" and not (isinstance(escalate, str) and escalate.strip()):
            veto = None  # the escalation it rested on is withdrawn
    else:
        receipt["scores"] = _scores(raw.get("scores"), rubric, texts)
        recommendation, escalate = raw.get("pass_recommendation"), raw.get("escalation")
        veto = _veto(raw.get("veto"), role, escalate, texts)
    receipt["recommendation"] = (re.sub(r"[ _]+", " ", recommendation.strip().upper())
                                 if isinstance(recommendation, str) and recommendation.strip() else None)
    receipt["escalate"] = escalate if isinstance(escalate, str) and escalate.strip() else None
    receipt["veto"] = veto
    receipt["written"] = now()
    path = _receipt_path(workspace, role, synthesis)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not synthesis:
        _receipt_path(workspace, role, True).unlink(missing_ok=True)
    write_json(path, receipt)
    return path


def current_receipts(workspace: Path, skill_sha: str, rubric_sha: str, synthesis: bool = False) -> dict[str, dict]:
    found = {role: read_json(_receipt_path(workspace, role, synthesis)) for role in ROLES}
    return {role: data for role, data in found.items()
            if data and data.get("skill_sha256") == skill_sha and data.get("rubric_sha256") == rubric_sha}


def rounds(workspace: Path) -> list[dict]:
    """The archived panel rounds, oldest first; round n lives in rounds/<n>/."""
    found = []
    while (entry := read_json(workspace / ROUNDS_DIR / str(len(found) + 1) / "round.json")) is not None:
        found.append(entry)
    return found


def check_round(workspace: Path, skill_sha: str) -> int:
    """The round a consensus on this skill belongs to (the last when the skill is unchanged since, else the
    next), or RoundsExhausted when that would pass `max_panel_rounds`."""
    archive = rounds(workspace)
    n = len(archive) if archive and archive[-1].get("skill_sha256") == skill_sha else len(archive) + 1
    if n > (cap := max_rounds(workspace)):
        raise RoundsExhausted(f"the panel ran its {cap} rounds (max_panel_rounds) and the skill changed since round "
                              f"{n - 1}; no round {n} starts. End the build UNSHIPPED: write `Verdict: UNSHIPPED` "
                              f"in BENCHMARK.md beside the last round's line: {archive[-1].get('line')}")
    return n


def exhausted(workspace: Path) -> dict | None:
    """The last round when the panel ran all `max_panel_rounds` and the skill changed since, else None."""
    archive = rounds(workspace)
    if len(archive) < max_rounds(workspace):
        return None
    try:
        current = skill_sha256(workspace, check_skill_name(archive[-1].get("skill")))
    except GateError:
        return archive[-1]
    return None if current == archive[-1].get("skill_sha256") else archive[-1]


def _overall(scores: dict[str, float], rubric: Rubric) -> float:
    return sum(scores[d.name] / d.high * d.weight for d in rubric.dimensions)


def disputes(receipts: dict[str, dict], rubric: Rubric) -> list[str]:
    """The dimensions a first round sends to synthesis (consensus-protocol.md, Disagreement Detection)."""
    trig, target = rules()["synthesis_triggers"], rubric.target_score
    overalls = [_overall({n: s["score"] for n, s in r["scores"].items()}, rubric) for r in receipts.values()]
    split = bool(overalls) and max(overalls) >= target and min(overalls) < target - trig["pass_fail_margin"]
    out = []
    for d in rubric.dimensions:
        scores = [r["scores"][d.name]["score"] for r in receipts.values()]
        confs = [r["scores"][d.name]["confidence"] for r in receipts.values()]
        spread = max(scores) - min(scores)
        if (spread >= trig["spread_at_least"] or receipts[DA]["scores"][d.name]["score"] <= trig["devils_advocate_at"]
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
    panel_round: int = 1
    max_panel_rounds: int = 3
    checkable: bool = False
    vetoes: list[str] = field(default_factory=list)

    def line(self) -> str:
        overall = "n/a" if self.overall is None else f"{self.overall:.3f}"
        allows = (f"review; veto: {', '.join(self.vetoes) or 'none'}" if self.checkable else
                  f"allows {self.allowed}")
        return (f"PANEL CONSENSUS: {self.label} ({allows}); overall={overall} "
                f"target={self.target:.2f}; blocked: {', '.join(self.blocked) or 'none'}; "
                f"deadlocked: {', '.join(self.deadlocked) or 'none'}; "
                f"escalate: {'yes' if self.escalate else 'no'}")


def _resolve(scores: list[float], within: float) -> tuple[float | None, str]:
    """(consensus score, how): all within `within` -> their mean; else the closest agreeing pair (the lower
    pair on a tie, the conservative reading) -> its mean; else deadlock (None)."""
    if max(scores) - min(scores) <= within:
        return sum(scores) / len(scores), "converged"
    pairs = sorted((abs(a - b), (a + b) / 2) for i, a in enumerate(scores) for b in scores[i + 1:]
                   if abs(a - b) <= within)
    return (pairs[0][1], "majority") if pairs else (None, "deadlock")


def compute(workspace: Path, skill: str, skill_sha: str) -> Consensus:
    """The consensus of the three current receipts; raises GateError naming what is missing, including a
    synthesis round the first round calls for, and RoundsExhausted past `max_panel_rounds`."""
    rubric, r = read_rubric(workspace), rules()
    panel_n = check_round(workspace, skill_sha)
    first = current_receipts(workspace, skill_sha, rubric.sha256)
    if missing := [role for role in ROLES if role not in first]:
        raise GateError(f"no current role receipt from {', '.join(missing)} for this skill and rubric: each "
                        "verifier runs `check_gate.py --panel-receipt <role> <scores.json>` after scoring (5.2)")
    disputed, final = disputes(first, rubric), first
    if disputed:
        final = current_receipts(workspace, skill_sha, rubric.sha256, synthesis=True)
        if waiting := [role for role in ROLES if role not in final]:
            raise GateError(f"SYNTHESIS NEEDED on {', '.join(disputed)}: run one synthesis round (5.3); "
                            f"{', '.join(waiting)} still to submit `--panel-receipt <role> <file> --synthesis`")
    result = Consensus(label="", allowed="", overall=None, target=rubric.target_score, disputed=disputed,
                       rounds=2 if disputed else 1, panel_round=panel_n, max_panel_rounds=max_rounds(workspace),
                       checkable=rubric.checkable)
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
    if rubric.checkable:  # a review: only a cited hard defect caps the verdict; check_gate.ship_rule decides the rest
        result.vetoes = [f"{role} {final[role]['veto']['kind']}" for role in ROLES if final[role].get("veto")]
        result.allowed = "UNSHIPPED" if result.vetoes else "SHIP"
    return result


def _receipts_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for role in ROLES:
        for synthesis in (False, True):
            digest.update(f"{role}{synthesis}".encode() + (file_sha256(_receipt_path(workspace, role, synthesis))
                                                           or "-").encode())
    return digest.hexdigest()


def write_consensus(workspace: Path, skill: str, skill_sha: str) -> Consensus:
    """Compute the consensus, write consensus.json, and archive it as this panel round (rewritten on the same
    skill) with SKILL.md and the final receipts."""
    result = compute(workspace, skill, skill_sha)
    rubric_sha = read_rubric(workspace).sha256
    write_json(workspace / CONSENSUS, {"skill": skill, "skill_sha256": skill_sha, "rubric_sha256": rubric_sha,
                                       "receipts_sha256": _receipts_digest(workspace), "line": result.line(),
                                       **asdict(result), "written": now()})
    folder = workspace / ROUNDS_DIR / str(result.panel_round)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    shutil.copyfile(workspace / "output" / skill / "SKILL.md", folder / "SKILL.md")
    for role, receipt in current_receipts(workspace, skill_sha, rubric_sha, synthesis=bool(result.disputed)).items():
        write_json(folder / f"{role}.json", receipt)
    write_json(folder / "round.json", {
        "round": result.panel_round, "skill": skill, "skill_sha256": skill_sha, "rubric_sha256": rubric_sha,
        "label": result.label, "allowed": result.allowed, "line": result.line(), "written": now()})
    return result


def recorded(workspace: Path, skill: str, skill_sha: str) -> tuple[Consensus | None, str]:
    """(the consensus, "ok") when consensus.json matches the current skill, rubric and receipts and a fresh
    computation; else (None, why)."""
    try:
        result = compute(workspace, skill, skill_sha)
        rubric_sha = read_rubric(workspace).sha256
    except GateError as exc:
        return None, str(exc)
    data = read_json(workspace / CONSENSUS)
    if data is None:
        return None, f"no {CONSENSUS.as_posix()}: run `check_gate.py --consensus` (5.3)"
    if (data.get("skill_sha256") != skill_sha or data.get("rubric_sha256") != rubric_sha
            or data.get("receipts_sha256") != _receipts_digest(workspace) or data.get("line") != result.line()):
        return None, (f"{CONSENSUS.as_posix()} is stale (the skill, rubric or a receipt changed since); re-run "
                      "`check_gate.py --consensus`")
    return result, "ok"
