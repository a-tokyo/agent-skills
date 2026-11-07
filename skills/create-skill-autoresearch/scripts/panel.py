"""Panel role receipts, rounds and the consensus they derive (SKILL.md 5.2-5.4), for check_gate.py.

A receipt (work/experiments/panel/<role>.json, <role>.synthesis.json) binds a verifier's scores to the skill and
rubric hashes, once every quote its evidence gives is found verbatim in output/<skill>/ or input/ (_verify_quote); each consensus is archived as a round under work/experiments/panel/rounds/<n>/ with a copy of
SKILL.md and the final receipts. The thresholds and max_panel_rounds live in references/verdict-rules.json;
the protocol in references/consensus-protocol.md; rubric.yaml's schema (read here by a small stdlib reader:
target_score, plateau_window, max_iterations, metric, and dimensions with name, weight, scale) in
references/rubric-templates.md.
Under the lite profile (gate_common.profile) the panel is one `reviewer` (references/panel/reviewer.md) holding
the Devil's Advocate's escalation, with no synthesis round; its receipt alone is the consensus. The full profile
accepts only the three roles, the lite one only the reviewer.
A receipt keeps the member's cited failure scenarios (`evidence` and `repro`) as `findings`, which a fix cycle may fix
(scripts/fix_cycle.py); once one opened, no panel receipt is accepted.
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
    file_sha256, is_number, lite_rules, log_attempt, now, profile, read_attempts, read_json, skill_sha256, write_json

RUBRIC = EVAL_DIR / "rubric.yaml"
PANEL_DIR = PurePosixPath("work") / "experiments" / "panel"
CONSENSUS = PANEL_DIR / "consensus.json"
ROUNDS_DIR = PANEL_DIR / "rounds"
RULES_PATH = Path(__file__).resolve().parent.parent / "references" / "verdict-rules.json"
DA = "devils-advocate"
ROLES = ("verifier-a", "verifier-b", DA)
REVIEWER = "reviewer"  # the lite profile's one-member panel: quality, utility and adversary in one role
LITE_ROLES = (REVIEWER,)
ALL_ROLES = ROLES + LITE_ROLES
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
    max_iterations: int = 20  # the Phase 4 budget: experiments after experiment 0


def read_rubric(workspace: Path) -> Rubric:
    path = workspace / RUBRIC
    if not path.is_file():
        raise GateError(f"{RUBRIC} not found; 2.3 writes it (schema: references/rubric-templates.md)")
    target: float | None = None
    window, budget = 2, 20
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
            elif key.strip() == "max_iterations":
                if not re.fullmatch(r"[1-9]\d*", _unquote(value)):
                    raise GateError(f"{RUBRIC}: max_iterations {value.strip()!r} is not a whole number >= 1")
                budget = int(_unquote(value))
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
    if profile(workspace) == "lite":  # small builders: the Phase 4 caps, the lower of rubric.yaml's and lite's
        window, budget = min(window, lite_rules()["plateau_window"]), min(budget, lite_rules()["max_iterations"])
    return Rubric(dims, rules()["default_target_score"] if target is None else target, file_sha256(path), window,
                  checkable, budget)


def checkable(workspace: Path) -> bool:
    """rubric.yaml says `metric: answer-key`; False when it is missing or unreadable (the panel-verdict path)."""
    try:
        return read_rubric(workspace).checkable
    except GateError:
        return False


def lite(workspace: Path) -> bool:
    return profile(workspace) == "lite"


def roles(workspace: Path) -> tuple[str, ...]:
    """The panel's roles for this build: the reviewer alone under the lite profile, else the three members."""
    return LITE_ROLES if lite(workspace) else ROLES


def adversary(workspace: Path) -> str:
    """The role that holds the Devil's Advocate's escalation."""
    return REVIEWER if lite(workspace) else DA


def max_rounds(workspace: Path) -> int:
    """max_panel_rounds for this build: metric_first's when its outputs are checkable, else the panel path's (the
    lite profile's on the lite panel: one ITERATE)."""
    if checkable(workspace):
        return metric_first()["max_panel_rounds"]
    return lite_rules()["max_panel_rounds"] if lite(workspace) else rules()["max_panel_rounds"]


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


def _veto(raw: object, role: str, escalate: object, texts: list[str], escalator: str = DA) -> dict | None:
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
    if raw["kind"] == "escalate" and (role != escalator or not (isinstance(escalate, str) and escalate.strip())):
        holder = "reviewer's" if escalator == REVIEWER else "Devil's Advocate's"
        raise GateError(f"an escalate veto is the {holder}, beside its `escalation`")
    _verify_quote("veto", evidence, texts)
    return {"kind": raw["kind"], "evidence": evidence, "repro": repro}


def write_receipt(workspace: Path, role: str, source: Path, skill: str, skill_sha: str,
                  synthesis: bool = False) -> Path:
    """Validate a verifier's scoring JSON against the rubric and write its role receipt."""
    allowed = roles(workspace)
    if role not in allowed:
        other = ("; it is the lite profile's one-member panel" if role in LITE_ROLES else
                 "; it is a member of the full profile's panel" if role in ROLES else "")
        raise GateError(f"role {role!r} is not one of {', '.join(allowed)} (this build's {profile(workspace)} "
                        f"profile panel{other})")
    if synthesis and lite(workspace):
        raise GateError("the lite profile's panel has no synthesis round: the reviewer's first receipt is final")
    import fix_cycle  # noqa: PLC0415  (fix_cycle imports this module)
    if fix_cycle.cycles(workspace):
        raise GateError("a fix cycle ran after the panel, and a fix cycle never re-runs it: the cycle's roles re-check "
                        "their findings with `check_gate.py --recheck-receipt`; run check_gate.py --next")
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
        if missing := [r for r in allowed if r not in current]:
            raise GateError(f"a synthesis receipt needs every first-round receipt for this skill; missing {missing}")
        if not (disputed := disputes(current, rubric)):
            raise GateError("the first round disputed nothing; there is no synthesis round")
        receipt["scores"] = _synthesis_scores(raw, current[role], disputed, rubric, texts)
        recommendation = raw.get("final_recommendation") or current[role]["recommendation"]
        receipt["findings"] = current[role].get("findings") or []
        escalate = current[role].get("escalate") if "escalation" not in raw else raw.get("escalation")
        veto = _veto(raw["veto"], role, escalate, texts, adversary(workspace)) if "veto" in raw else current[role].get("veto")
        if veto and veto["kind"] == "escalate" and not (isinstance(escalate, str) and escalate.strip()):
            veto = None  # the escalation it rested on is withdrawn
    else:
        receipt["scores"] = _scores(raw.get("scores"), rubric, texts)
        receipt["findings"] = fix_cycle.cited(raw.get("failure_scenarios"), texts)
        recommendation, escalate = raw.get("pass_recommendation"), raw.get("escalation")
        veto = _veto(raw.get("veto"), role, escalate, texts, adversary(workspace))
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
    if not synthesis:
        log_attempt(workspace, "panel", role=role, skill=skill, skill_sha256=skill_sha)
    return path


def current_receipts(workspace: Path, skill_sha: str, rubric_sha: str, synthesis: bool = False) -> dict[str, dict]:
    """The receipts of this build's panel roles (roles) bound to this skill and rubric."""
    found = {role: read_json(_receipt_path(workspace, role, synthesis)) for role in roles(workspace)}
    return {role: data for role, data in found.items()
            if data and data.get("skill_sha256") == skill_sha and data.get("rubric_sha256") == rubric_sha}


def rounds(workspace: Path) -> list[dict]:
    """The archived panel rounds, oldest first; round n lives in rounds/<n>/."""
    found = []
    while (entry := read_json(workspace / ROUNDS_DIR / str(len(found) + 1) / "round.json")) is not None:
        found.append(entry)
    return found


def started(workspace: Path) -> list[tuple[str, str]]:
    """(skill sha, skill) of every panel round started, oldest first, once per skill hash: a round starts with its
    first receipt (gate-attempts.jsonl `panel` lines, which survive a receipt being overwritten), archived rounds
    and the receipt files on disk."""
    found: dict[str, str] = {}
    entries = [e for e in read_attempts(workspace) if e.get("step") == "panel"] + rounds(workspace) + [
        r for r in (read_json(workspace / PANEL_DIR / f"{role}.json") for role in ALL_ROLES) if r]
    for entry in entries:
        sha, skill = entry.get("skill_sha256"), entry.get("skill")
        if isinstance(sha, str) and sha and isinstance(skill, str):
            found.setdefault(sha, skill)
    return list(found.items())


def check_round(workspace: Path, skill_sha: str) -> int:
    """The round a consensus on this skill belongs to (the last when the skill is unchanged since, else the
    next), or RoundsExhausted when the panel already started `max_panel_rounds` rounds on other versions of the
    skill (exhausted)."""
    archive = rounds(workspace)
    if archive and archive[-1].get("skill_sha256") == skill_sha:
        return len(archive)  # the last round's own skill: no new round starts
    n = len(archive) + 1
    last = exhausted(workspace)
    if n > (cap := max_rounds(workspace)) or last is not None:
        line = (last or archive[-1]).get("line")
        raise RoundsExhausted(f"the panel ran its {cap} rounds (max_panel_rounds) and the skill changed since; no "
                              "new round starts. End the build UNSHIPPED: write `Verdict: UNSHIPPED` in BENCHMARK.md"
                              + (f" beside the last round's line: {line}" if line else ""))
    return n


def exhausted(workspace: Path) -> dict | None:
    """The last round when the panel started all `max_panel_rounds` rounds (started) and the skill as it is now
    is none of the versions they reviewed, else None. A round with receipts but no consensus counts: on the
    metric-first path (one round) any change after the first receipt ends the build. Once a fix cycle opened, on
    either path: None only while the cycle is open or closed on the skill as it is now (fix_cycle.governs)."""
    import fix_cycle  # noqa: PLC0415  (fix_cycle imports this module)
    begun, archive, cycled = started(workspace), rounds(workspace), bool(fix_cycle.cycles(workspace))
    if len(begun) < max_rounds(workspace) and not cycled or not begun:
        return None  # after a fix cycle no new round starts on either path, whatever rounds are left
    last = archive[-1] if archive else {"line": "", "skill": begun[-1][1], "skill_sha256": begun[-1][0]}
    try:
        current = skill_sha256(workspace, check_skill_name(begun[-1][1]))
    except GateError:
        return last
    if cycled:
        return None if fix_cycle.governs(workspace, current) else last
    return None if current in dict(begun) else last


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
    rubric, r, members = read_rubric(workspace), rules(), roles(workspace)
    panel_n = check_round(workspace, skill_sha)
    first = current_receipts(workspace, skill_sha, rubric.sha256)
    if missing := [role for role in members if role not in first]:
        raise GateError(f"no current role receipt from {', '.join(missing)} for this skill and rubric: each "
                        "verifier runs `check_gate.py --panel-receipt <role> <scores.json>` after scoring (5.2)")
    disputed, final = ([] if lite(workspace) else disputes(first, rubric)), first
    if disputed:
        final = current_receipts(workspace, skill_sha, rubric.sha256, synthesis=True)
        if waiting := [role for role in members if role not in final]:
            raise GateError(f"SYNTHESIS NEEDED on {', '.join(disputed)}: run one synthesis round (5.3); "
                            f"{', '.join(waiting)} still to submit `--panel-receipt <role> <file> --synthesis`")
    result = Consensus(label="", allowed="", overall=None, target=rubric.target_score, disputed=disputed,
                       rounds=2 if disputed else 1, panel_round=panel_n, max_panel_rounds=max_rounds(workspace),
                       checkable=rubric.checkable)
    for d in rubric.dimensions:
        scores = [final[role]["scores"][d.name]["score"] for role in members]
        value, how = _resolve(scores, r["agree_within"])
        result.dimensions[d.name] = None if value is None else round(value, 4)
        if how == "deadlock":
            result.deadlocked.append(d.name)
        elif how == "majority":
            result.dissent[d.name] = ", ".join(f"{role} {s:g}" for role, s in zip(members, scores))
        if sum(1 for s in scores if s < r["block_below"]) >= min(r["block_majority"], len(members)):
            result.blocked.append(d.name)
    result.escalate = final[adversary(workspace)].get("escalate")
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
        result.vetoes = [f"{role} {final[role]['veto']['kind']}" for role in members if final[role].get("veto")]
        result.allowed = "UNSHIPPED" if result.vetoes else "SHIP"
    return result


def _receipts_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for role in roles(workspace):  # the full profile's digest is the one consensus.json files already record
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


if __name__ == "__main__":
    import sys

    USAGE = ("panel.py is a library, not a command: check_gate.py runs it.\n\n"
             "    check_gate.py --panel                                     # 5.2: validate the gate first\n"
             "    check_gate.py --panel-receipt ROLE SCORES_JSON [--synthesis]  # each member, after it scores\n"
             "    check_gate.py --consensus                                 # 5.3: the verdict from the receipts\n\n"
             "ROLE: verifier-a, verifier-b or devils-advocate. See check_gate.py --help.\n\n")
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(USAGE + __doc__.strip())
        sys.exit(0)
    sys.stderr.write(USAGE)
    sys.exit(2)
