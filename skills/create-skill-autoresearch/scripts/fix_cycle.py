"""Fix cycles after the panel (SKILL.md 5.4, references/fix-cycle.md), for check_gate.py, gate.py and panel.py.

Once the panel reviewed the skill, the build may fix the panel's cited findings instead of editing freely, up to
`max_fix_cycles` cycles (references/verdict-rules.json `fix_cycle`), whatever the consensus label:

  - a finding is a receipt's `veto` (`<role>.veto`) or a failure scenario the receipt cites with a verbatim
    `evidence` quote and a `repro` (`<role>.F<i>`), on either path;
  - a finding is blocking when its resolution can lift the verdict: a veto on the metric-first path; every finding
    under an ITERATE, BLOCK or ESCALATE label on the panel-verdict path. --next pushes a cycle only for blocking
    findings or a skill already changed after the panel (the cycle adopts that change); any other finding may
    open one on request (the freeze guard's deny message names the command);
  - `check_gate.py --fix-cycle [--finding ID ...]` opens cycle n: fix-cycles/<n>/cycle.json records the findings,
    the base commit (the skill the findings are against) and the gate-input hashes; work/experiments/fix-cycle-<n>.md
    gets a section per finding id for the fix notes and the lines the fix touches (`lines: <path>:<first>-<last>`,
    or `lines: none` for a finding the cycle leaves alone);
  - the fix is ONE commit on top of the base, touching only declared lines (fix_status); gate.py re-gates only
    then (gate_refusal), rescore.py scores it, and the gate inputs must be the cycle's;
  - each role that raised a finding re-checks only its own findings on the fixed skill and writes a re-check
    receipt bound to the fixed skill's hash (`check_gate.py --recheck-receipt ROLE [JSON]`, write_recheck);
  - the ship rule (rule) never exceeds the original consensus: metric-first, the gate and the metric decide; on the
    panel-verdict path SHIP and SHIP WITH CAVEATS keep their cap and ITERATE, BLOCK or ESCALATE cap at SHIP WITH
    CAVEATS. Resolved findings only lift vetoes: an unresolved blocking finding, or a regression a re-check names,
    keeps UNSHIPPED (another cycle while cycles remain); an unresolved plain finding changes nothing.

A fix cycle never re-runs the panel or adds dimensions; outside a cycle a skill change after the panel still ends
the build UNSHIPPED (panel.exhausted), unless a cycle can adopt it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import panel
from gate_common import (
    DATA_SPLIT, RUN_CASE, GateError, cases_sha256, file_sha256, inputs_sha256, log_attempt, now, read_data_split,
    read_json, skill_sha256, split_sha256, write_json, write_text,
)

CYCLES_DIR = panel.PANEL_DIR / "fix-cycles"
NOTES_DIR = PurePosixPath("work") / "experiments"
UNSHIPPING = ("ITERATE", "BLOCK", "ESCALATE")  # panel-verdict labels that allow only UNSHIPPED
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
DECLARED = re.compile(r"^(.+?)(?::(\d+)(?:-(\d+))?)?$")
IGNORED = ("__pycache__", ".pyc", ".DS_Store")  # as tree_sha256 skips them


def max_cycles() -> int:
    return int(panel.rules().get("max_fix_cycles", 0))


def notes_path(workspace: Path, n: int) -> Path:
    return workspace / NOTES_DIR / f"fix-cycle-{n}.md"


def cycle_dir(workspace: Path, n: int) -> Path:
    return workspace / CYCLES_DIR / str(n)


def cycles(workspace: Path) -> list[dict]:
    """The opened fix cycles, oldest first; cycle n lives in fix-cycles/<n>/cycle.json."""
    found: list[dict] = []
    while (entry := read_json(cycle_dir(workspace, len(found) + 1) / "cycle.json")) is not None:
        found.append(entry)
    return found


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None


# Findings ---------------------------------------------------------------------------------------------------------

def cited(raw: object, texts: list[str]) -> list[dict]:
    """A member's cited failure scenarios (its JSON's `failure_scenarios` entries carrying both `evidence` and
    `repro`), each quote verified like a score's, numbered F1.. in order; the rest are not findings."""
    out = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        evidence, repro = entry.get("evidence"), entry.get("repro")
        if not (isinstance(evidence, str) and evidence.strip() and isinstance(repro, str) and repro.strip()):
            continue
        panel._verify_quote(f"failure scenario F{len(out) + 1}", evidence, texts)
        scenario = entry.get("scenario")
        out.append({"id": f"F{len(out) + 1}", "scenario": scenario if isinstance(scenario, str) else "",
                    "severity": entry.get("severity") if isinstance(entry.get("severity"), str) else "",
                    "evidence": evidence, "repro": repro})
    return out


def findings(receipts: dict[str, dict]) -> list[dict]:
    """The findings a fix cycle may fix: every receipt's veto and its cited scenarios."""
    out = []
    for role in panel.ALL_ROLES:
        receipt = receipts.get(role)
        if not receipt:
            continue
        if veto := receipt.get("veto"):
            out.append({"id": f"{role}.veto", "role": role, "kind": veto["kind"], "evidence": veto["evidence"],
                        "repro": veto["repro"], "scenario": receipt.get("escalate") or ""})
        for found in receipt.get("findings") or []:
            out.append({**found, "id": f"{role}.{found['id']}", "role": role, "kind": "finding"})
    return out


# State ------------------------------------------------------------------------------------------------------------

def rechecks(workspace: Path, cycle: dict) -> dict[str, dict]:
    folder = cycle_dir(workspace, cycle["n"])
    return {role: data for role in cycle["roles"] if (data := read_json(folder / f"{role}.json"))}


def closed(workspace: Path, cycle: dict) -> str | None:
    """The fixed skill's hash once every role of the cycle re-checked that same version, else None (open)."""
    got = rechecks(workspace, cycle)
    shas = {r.get("skill_sha256") for r in got.values()}
    return shas.pop() if len(got) == len(cycle["roles"]) and len(shas) == 1 else None


def pending(workspace: Path, cycle: dict, skill_sha: str) -> list[str]:
    """The cycle's roles with no re-check receipt on the skill as it is now."""
    got = rechecks(workspace, cycle)
    return [role for role in cycle["roles"] if got.get(role, {}).get("skill_sha256") != skill_sha]


def outcome(workspace: Path, cycle: dict) -> tuple[list[str], list[str]]:
    """(resolved, unresolved) finding ids of a cycle, from its re-check receipts."""
    got, resolved, unresolved = rechecks(workspace, cycle), [], []
    for found in cycle["findings"]:
        result = (got.get(found["role"]) or {}).get("results", {}).get(found["id"]) or {}
        (resolved if result.get("resolved") is True else unresolved).append(found["id"])
    return resolved, unresolved


def governs(workspace: Path, skill_sha: str) -> bool:
    """A fix cycle decides what the skill as it is now may do: the latest is open, or closed on this version."""
    found = cycles(workspace)
    if not found:
        return False
    fixed = closed(workspace, found[-1])
    return fixed is None or fixed == skill_sha


def _inputs(workspace: Path) -> dict:
    """Everything a gate reads besides the skill: a fix cycle may change none of it."""
    try:
        split = read_data_split(workspace / DATA_SPLIT)
    except GateError:
        return {"inputs": inputs_sha256(workspace), "split": None}
    return {"inputs": inputs_sha256(workspace), "cases": cases_sha256(workspace, split.plugin_eval or []),
            "split": split_sha256(split), "run_case": file_sha256(workspace / RUN_CASE)}


# Offer and open ---------------------------------------------------------------------------------------------------

@dataclass
class Offer:
    n: int
    reviewed_sha: str  # the skill the panel reviewed
    base_sha: str  # the skill these findings are against (the reviewed one, or the last cycle's fix)
    findings: list[dict] = field(default_factory=list)
    why: str = ""
    blocking: list[str] = field(default_factory=list)  # ids whose resolution can lift the verdict
    adopt: bool = False  # the skill already changed since base_sha: the cycle adopts that change as its fix

    @property
    def pushed(self) -> bool:
        """--next sends the build into this cycle: a blocking finding is left, or a change waits to be adopted."""
        return bool(self.blocking) or self.adopt


def blocking(consensus: panel.Consensus, listed: list[dict]) -> list[str]:
    """The finding ids whose resolution can lift the verdict: the vetoes (metric-first), or every finding under
    an ITERATE, BLOCK or ESCALATE label (panel-verdict path); none under SHIP or SHIP WITH CAVEATS."""
    if consensus.checkable:
        return [f["id"] for f in listed if f["kind"] != "finding"]
    return [f["id"] for f in listed] if consensus.label in UNSHIPPING else []


def latest(workspace: Path, found: list[dict]) -> dict[str, dict]:
    """{finding id: its newest re-check result} over the cycles `found`."""
    out: dict[str, dict] = {}
    for cycle in found:
        got = rechecks(workspace, cycle)
        for f in cycle["findings"]:
            if (result := (got.get(f["role"]) or {}).get("results", {}).get(f["id"])) is not None:
                out[f["id"]] = result
    return out


def standing(workspace: Path, found: list[dict]) -> tuple[list[dict], list[str]]:
    """(the panel's findings no cycle resolved yet, the ids still holding the verdict down: unresolved blocking
    findings and findings whose newest re-check names a regression)."""
    first = found[0]
    pool = first.get("panel_findings") or first["findings"]
    holds = first.get("blocking", [f["id"] for f in first["findings"]])  # a pre-r22 cycle: every finding
    results = latest(workspace, found)
    left = [f for f in pool if (results.get(f["id"]) or {}).get("resolved") is not True]
    held = [f["id"] for f in left if f["id"] in holds or (results.get(f["id"]) or {}).get("regression")]
    return left, held


def reviewed(workspace: Path, skill: str) -> tuple[str | None, panel.Consensus | None]:
    """(the skill hash the panel's latest round reviewed, its consensus): consensus.json's when it is that round's,
    else computed from the round's receipts (the skill may have changed before the consensus was written)."""
    begun = [sha for sha, name in panel.started(workspace) if name == skill]
    data = read_json(workspace / panel.CONSENSUS) or {}
    sha = begun[-1] if begun else data.get("skill_sha256") if data.get("skill") == skill else None
    if not isinstance(sha, str) or not sha:
        return None, None
    consensus, _ = panel.recorded(workspace, skill, sha)
    if consensus is None:
        try:
            consensus = panel.compute(workspace, skill, sha)
        except GateError:
            return sha, None
    return sha, consensus


def _gate_allows(workspace: Path, skill: str, sha: str, adopt: bool) -> bool:
    """False when a valid gate on the reviewed skill (or, adopting, the skill as it is now) is at UNSHIPPED;
    without a valid gate only an adopting cycle goes on (it re-gates its fix)."""
    import check_gate  # noqa: PLC0415  (check_gate imports this module)
    gate = check_gate.validate(workspace, skill, expect_sha=sha)
    if not gate.valid and adopt:
        gate = check_gate.validate(workspace, skill)
        if not gate.valid:
            return True
    return gate.valid and check_gate.gate_level(gate)[0] != "UNSHIPPED"


def offer(workspace: Path, skill: str) -> Offer | None:
    """The next fix cycle the build may open, or None. Cycle 1: the panel's round left cited findings (any
    label; an ITERATE or BLOCK with Phase 4 left returns to Phase 4 instead) and the gate and metric levels are not
    UNSHIPPED already. Cycle n+1: cycle n closed with panel findings unresolved. Either adopts a skill change made
    since its base. Never past max_fix_cycles."""
    import check_gate  # noqa: PLC0415  (check_gate imports this module)
    cap, found, current = max_cycles(), cycles(workspace), skill_sha256(workspace, skill)
    if found:
        last = found[-1]
        fixed = closed(workspace, last)
        left, held = standing(workspace, found)
        if fixed is None or last["n"] >= cap or not left:
            return None
        adopt = fixed != current
        if not _gate_allows(workspace, skill, fixed, adopt):
            return None
        value, _ = check_gate.ship_metric(workspace, skill)
        if not adopt and _metric_level(workspace, value) == "UNSHIPPED":
            return None
        _, unresolved = outcome(workspace, last)
        return Offer(last["n"] + 1, last["reviewed_sha"], fixed, left,
                     f"cycle {last['n']} left {', '.join(unresolved or [f['id'] for f in left])} unresolved",
                     held, adopt)
    if cap < 1:
        return None
    sha, consensus = reviewed(workspace, skill)
    if consensus is None:
        return None
    if not consensus.checkable and consensus.label in ("ITERATE", "BLOCK") and not (
            check_gate._spent(workspace) or consensus.panel_round >= consensus.max_panel_rounds):
        return None  # the panel-verdict path still has its Phase 4 return (5.4 ITERATE)
    adopt = current != sha
    if not _gate_allows(workspace, skill, sha, adopt):
        return None
    if consensus.checkable and not adopt:
        value, _ = check_gate.ship_metric(workspace, skill)
        if value is None or _metric_level(workspace, value) == "UNSHIPPED":
            return None  # unscored: rescore.py first; below target: fixing a finding cannot ship it
    receipts = panel.current_receipts(workspace, sha, panel.read_rubric(workspace).sha256,
                                      synthesis=bool(consensus.disputed))
    if not (listed := findings(receipts)):
        return None
    held = blocking(consensus, listed)
    what = (f"veto {', '.join(consensus.vetoes)}" if consensus.checkable and consensus.vetoes else
            f"consensus {consensus.label}")
    why = (f"the panel's {what} rests on cited findings" if held else
           f"the panel's {what} cites findings; a fix cannot raise the verdict above it")
    return Offer(1, sha, sha, listed, why, held, adopt)


def _metric_level(workspace: Path, value: float | None) -> str:
    import check_gate  # noqa: PLC0415
    rubric = panel.read_rubric(workspace)
    return check_gate._level(value, rubric.target_score, panel.rules()["caveat_margin"])


def commit_skill_sha(workspace: Path, commit: str, skill: str) -> str | None:
    """tree_sha256 of output/<skill>/ as `commit` holds it (the files git tracks there), or None."""
    import hashlib  # noqa: PLC0415
    listing = _git(workspace, "ls-tree", "-r", "-z", "--full-tree", commit, "--", f"output/{skill}")
    if listing is None or listing.returncode != 0:
        return None
    prefix, entries = f"output/{skill}/", []
    for item in filter(None, listing.stdout.split("\0")):
        meta, _, path = item.partition("\t")
        rel = path[len(prefix):] if path.startswith(prefix) else None
        if rel and meta.split()[1] == "blob" and not any(part in IGNORED or part.endswith(".pyc")
                                                         for part in rel.split("/")):
            entries.append((rel, meta.split()[2]))
    digest = hashlib.sha256()
    for rel, blob in sorted(entries):
        body = subprocess.run(["git", "-C", str(workspace), "cat-file", "blob", blob], capture_output=True, timeout=60)
        if body.returncode != 0:
            return None
        digest.update(rel.encode() + b"\0")
        digest.update(hashlib.sha256(body.stdout).digest())
    return digest.hexdigest()


def _base_commit(workspace: Path, skill: str, sha: str) -> str | None:
    """The newest commit holding output/<skill>/ at `sha`: HEAD, a commit that changed output/<skill>/ (the
    newest 200), else a commit the experiment log names."""
    import check_gate  # noqa: PLC0415
    _, experiments, _ = check_gate._experiments(workspace)
    touched = _git(workspace, "rev-list", "--max-count=200", "HEAD", "--", f"output/{skill}")
    seen: list[str] = []
    for ref in ["HEAD", *(touched.stdout.split() if touched is not None and touched.returncode == 0 else []),
                *reversed([e["commit"].strip() for e in experiments if isinstance(e.get("commit"), str)])]:
        rev = _git(workspace, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        commit = rev.stdout.strip() if rev is not None and rev.returncode == 0 else ""
        if commit and commit not in seen:
            seen.append(commit)
            if commit_skill_sha(workspace, commit, skill) == sha:
                return commit
    return None


def _cited_files(workspace: Path, base: str, skill: str, finding: dict) -> set[str]:
    """The files under output/<skill>/ (as `base` holds them) a finding cites: named in its evidence, or holding
    one of its quotes."""
    listing = _git(workspace, "ls-tree", "-r", "--name-only", "--full-tree", base, "--", f"output/{skill}")
    prefix, out = f"output/{skill}/", set()
    files = [f[len(prefix):] for f in (listing.stdout.split("\n") if listing is not None else []) if f.startswith(prefix)]
    evidence = finding.get("evidence") or ""
    quotes = [panel._spaced(a or b) for a, b in panel.QUOTED.findall(evidence)] or [panel._spaced(evidence)]
    for rel in files:
        if rel in evidence:
            out.add(rel)
            continue
        body = _git(workspace, "show", f"{base}:{prefix}{rel}")
        text = panel._spaced(body.stdout) if body is not None and body.returncode == 0 else ""
        if any(q and q in text for q in quotes):
            out.add(rel)
    return out


def _changed(diff: str, prefix: str) -> dict[str, list[str]]:
    """{path under output/<skill>/: its changed spans as `lines:` items} from a `git diff -U0`."""
    out: dict[str, list[str]] = {}
    old, path = "", None
    for line in diff.splitlines():
        if line.startswith("--- "):
            old = line[4:]
        elif line.startswith("+++ "):
            new = line[4:]
            path = (old if new == "/dev/null" else new)[2:].removeprefix(prefix)
            if "/dev/null" in (old, new):
                out[path] = [path]
                path = None
        elif line.startswith("Binary files ") and line.endswith(" differ"):
            name = line.split(" and ")[-1].removesuffix(" differ").removeprefix("b/").removeprefix("a/")
            out[name.removeprefix(prefix)] = [name.removeprefix(prefix)]
        elif (match := HUNK.match(line)) and path is not None:
            start, count = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
            last = start + max(count, 1) - 1
            out.setdefault(path, []).append(f"{path}:{start}-{last}")
    return out


def adoption(workspace: Path, skill: str, base: str, listed: list[dict]) -> dict[str, list[str]]:
    """{finding id: the `lines:` items of the change since `base` in the files it cites}, for a cycle that adopts
    a change already made (committed or not); a finding citing no changed file is absent."""
    diff = _git(workspace, "diff", "-U0", "--no-renames", "--no-color", "--no-ext-diff", base, "--",
                f"output/{skill}")
    if diff is None or diff.returncode != 0:
        return {}
    changed = _changed(diff.stdout, f"output/{skill}/")
    out = {}
    for f in listed:
        items = [item for rel in sorted(_cited_files(workspace, base, skill, f) & changed.keys())
                 for item in changed[rel]]
        if items:
            out[f["id"]] = items
    return out


def open_cycle(workspace: Path, skill: str, chosen: list[str] | None = None) -> dict:
    """Open the cycle offer() allows, on all its findings or the `chosen` ids: write its record and the notes
    skeleton (kept if the builder wrote one); a cycle adopting a change made since its base declares that change's
    lines under the findings whose files it touches."""
    found = offer(workspace, skill)
    if found is None:
        raise GateError("no fix cycle to open: it needs a panel round with cited findings (a veto, or a failure "
                        "scenario with a verbatim `evidence` quote and a `repro`), a gate and metric that are not "
                        f"UNSHIPPED, and fewer than {max_cycles()} cycles (max_fix_cycles); run check_gate.py --next")
    ids = [f["id"] for f in found.findings]
    if chosen and (unknown := [c for c in chosen if c not in ids]):
        raise GateError(f"--finding {', '.join(unknown)} is not an open finding; this cycle may fix: {', '.join(ids)}")
    base = _base_commit(workspace, skill, found.base_sha)
    if base is None:
        raise GateError(f"no commit holds output/{skill}/ as the panel (or the last cycle) saw it; the fix is one "
                        "commit on top of that version: commit the reviewed skill, or restore it and commit it")
    adopted = adoption(workspace, skill, base, found.findings) if found.adopt else {}
    picked = [f for f in found.findings if f["id"] in (chosen or adopted or ids)]
    if found.n == 1 and panel.recorded(workspace, skill, found.reviewed_sha)[0] is None:
        panel.write_consensus(workspace, skill, found.reviewed_sha)  # the round's record, for rule() and the lines
    roles = [role for role in panel.ALL_ROLES if any(f["role"] == role for f in picked)]
    first = cycles(workspace)[0] if found.n > 1 else {}
    record = {"n": found.n, "skill": skill, "reviewed_sha": found.reviewed_sha, "base_sha": found.base_sha,
              "base_commit": base, "findings": picked, "roles": roles, "inputs": _inputs(workspace),
              "blocking": first.get("blocking", found.blocking),
              "panel_findings": first.get("panel_findings") or found.findings,
              "adopted": bool(found.adopt), "opened": now()}
    folder = cycle_dir(workspace, found.n)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "cycle.json", record)
    notes = notes_path(workspace, found.n)
    if not notes.is_file():
        notes.parent.mkdir(parents=True, exist_ok=True)
        sections = "".join(
            f"\n## {f['id']}\n\nFinding: {f['evidence']} (repro: {f['repro']})\n"
            + (f"Fix: (adopted: the change made since the base)\nlines: {', '.join(adopted[f['id']])}\n"
               if f["id"] in adopted else "Fix: \nlines: \n")
            for f in picked)
        write_text(notes, f"# Fix cycle {found.n}\n\nFix only what each finding cites. `lines:` lists every line the "
                          f"fix commit touches, as `<path under output/{skill}/>:<first>-<last>` (new-file line "
                          "numbers; comma-separated; a file the commit adds or deletes: its path alone; `none` for a "
                          "finding this cycle leaves alone).\n" + sections)
    log_attempt(workspace, "fix_cycle", cycle=found.n, skill=skill, findings=[f["id"] for f in picked],
                adopted=bool(found.adopt))
    return record


# The fix commit ---------------------------------------------------------------------------------------------------

def _declared(workspace: Path, cycle: dict) -> tuple[dict[str, list[tuple[int, int] | None]], list[str]]:
    """({path under output/<skill>/: declared spans, None = the whole file}, finding ids with no `lines:`)."""
    path, spans, has = notes_path(workspace, cycle["n"]), {}, set()
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    ids, prefix = {f["id"] for f in cycle["findings"]}, f"output/{cycle['skill']}/"
    for section in re.split(r"(?m)^##[ \t]+", text)[1:]:
        head, _, body = section.partition("\n")
        name = head.strip().strip("`")
        if name not in ids:
            continue  # only a finding's section declares lines
        for listed in re.findall(r"(?im)^[ \t*-]*lines:[ \t]*(.*)$", body):
            if listed.strip().strip("`").lower() == "none":
                has.add(name)  # left alone this cycle: it stays unresolved
                continue
            for item in (i.strip().strip("`") for i in listed.split(",")):
                if not item or not (match := DECLARED.fullmatch(item)):
                    continue
                file = match.group(1).strip().removeprefix(prefix)
                first = match.group(2)
                span = None if first is None else (int(first), int(match.group(3) or first))
                spans.setdefault(file, []).append(span)
                has.add(name)
    return spans, [f["id"] for f in cycle["findings"] if f["id"] not in has]


def _outside(diff: str, spans: dict[str, list], prefix: str) -> list[str]:
    """`<path>:<line>` for each changed line (new-file numbering) no declared span covers; a pure deletion after
    line k needs a span that reaches k or k+1; an added, deleted or binary file needs its path declared alone."""
    bad, old, path, whole = [], "", None, False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, whole = None, False
        elif line.startswith("--- "):
            old = line[4:]
        elif line.startswith("+++ "):
            new = line[4:]
            whole = "/dev/null" in (old, new)
            path = (old if new == "/dev/null" else new)[2:].removeprefix(prefix)
            if whole and None not in spans.get(path, []):
                bad.append(f"{path} (added or deleted: declare its path alone)")
        elif line.startswith("Binary files ") and line.endswith(" differ"):
            name = line.split(" and ")[-1].removesuffix(" differ").removeprefix("b/").removeprefix("a/")
            name = name.removeprefix(prefix)
            if None not in spans.get(name, []):
                bad.append(f"{name} (binary: declare its path alone)")
        elif (match := HUNK.match(line)) and path is not None and not whole:
            start, count = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
            ranges = [s for s in spans.get(path, []) if s is not None]
            if count == 0:
                if not any(a - 1 <= start <= b for a, b in ranges):
                    bad.append(f"{path}:{start} (a deletion)")
            elif missing := [n for n in range(start, start + count) if not any(a <= n <= b for a, b in ranges)]:
                bad.append(f"{path}:{missing[0]}" + (f"-{missing[-1]}" if len(missing) > 1 else ""))
    return bad


def _dirty(workspace: Path, out: str) -> list[str]:
    status = _git(workspace, "status", "--porcelain", "--untracked-files=all", "--", out)
    if status is None or status.returncode != 0:
        return ["(git status failed)"]
    return [line[3:] for line in status.stdout.splitlines()
            if line.strip() and not any(part in line for part in IGNORED)]


@dataclass
class FixStatus:
    code: str  # ok | none | many | dirty | notes | outside | inputs | git
    why: str
    commit: str | None = None


def fix_status(workspace: Path, cycle: dict) -> FixStatus:
    """Whether the cycle's fix is one commit on top of its base that touches only declared lines, with output/
    clean and the gate inputs unchanged since the cycle opened."""
    skill, base, n = cycle["skill"], cycle["base_commit"], cycle["n"]
    out = f"output/{skill}"
    if _inputs(workspace) != cycle["inputs"]:
        return FixStatus("inputs", f"a gate input changed since fix cycle {n} opened (input/, work/evaluation/, the "
                                   "split or run-case.sh); a fix cycle changes only output/: restore it")
    revs = _git(workspace, "rev-list", f"{base}..HEAD", "--", out)
    if revs is None or revs.returncode != 0:
        return FixStatus("git", f"git cannot list the commits since the cycle's base {base[:12]}")
    commits, dirty = revs.stdout.split(), _dirty(workspace, out)
    if not commits:
        return FixStatus("none", f"no fix commit yet on top of {base[:12]}" + (
            f" ({len(dirty)} uncommitted change(s) under {out}/)" if dirty else ""))
    if len(commits) > 1 or dirty:
        return FixStatus("many", (f"{len(commits)} commits touch {out}/ since {base[:12]}" if len(commits) > 1 else
                                  f"uncommitted changes under {out}/ ({', '.join(dirty[:3])})") +
                         "; the fix is exactly one commit")
    spans, missing = _declared(workspace, cycle)
    notes = notes_path(workspace, n).relative_to(workspace).as_posix()
    if missing:
        return FixStatus("notes", f"{notes} declares no `lines:` for {', '.join(missing)}", commits[0])
    diff = _git(workspace, "diff", "-U0", "--no-renames", "--no-color", "--no-ext-diff", base, commits[0], "--", out)
    if diff is None or diff.returncode != 0:
        return FixStatus("git", f"git cannot diff {base[:12]}..{commits[0][:12]}", commits[0])
    if bad := _outside(diff.stdout, spans, f"{out}/"):
        return FixStatus("outside", f"the fix commit touches lines no finding in {notes} declares: "
                                    f"{', '.join(bad[:6])}" + (f" and {len(bad) - 6} more" if len(bad) > 6 else ""),
                         commits[0])
    return FixStatus("ok", f"fix commit {commits[0][:12]} touches only the declared lines", commits[0])


def gate_refusal(workspace: Path) -> str | None:
    """Why gate.py may not start inside an open fix cycle (its fix commit is not valid), else None."""
    found = cycles(workspace)
    if not found or closed(workspace, found[-1]) is not None:
        return None
    status = fix_status(workspace, found[-1])
    return None if status.code == "ok" else f"fix cycle {found[-1]['n']} is open: {status.why}"


# The re-check -----------------------------------------------------------------------------------------------------

def open_now(workspace: Path) -> dict | None:
    found = cycles(workspace)
    return found[-1] if found and closed(workspace, found[-1]) is None else None


def write_recheck(workspace: Path, role: str, source: Path, skill: str) -> tuple[Path, list[str], list[str]]:
    """Validate a re-check JSON ({"findings": {id: {"resolved", "evidence", "regression"}}}) for `role`'s findings
    in the open cycle and write its receipt, bound to the fixed skill's hash; (path, resolved, unresolved)."""
    import check_gate  # noqa: PLC0415
    cycle = open_now(workspace)
    if cycle is None:
        raise GateError("no fix cycle is open (check_gate.py --next opens one when the panel's findings allow it)")
    if role not in cycle["roles"]:
        raise GateError(f"role {role!r} raised no finding in fix cycle {cycle['n']}; its roles: "
                        f"{', '.join(cycle['roles'])}")
    status = fix_status(workspace, cycle)
    if status.code != "ok":
        raise GateError(status.why)
    if (workspace / "work" / "evaluation" / "evaluate.sh").is_file() and \
            check_gate.ship_metric(workspace, skill)[0] is None:
        raise GateError(f"the fixed skill has no score yet; run first: {check_gate.rescore_command(workspace)}")
    raw = read_json(source)
    results = raw.get("findings") if raw else None
    mine = [f["id"] for f in cycle["findings"] if f["role"] == role]
    if not isinstance(results, dict) or set(results) != set(mine):
        got = sorted(results) if isinstance(results, dict) else results
        raise GateError(f"a re-check JSON's `findings` covers exactly {role}'s findings {mine}; got {got}")
    texts, out, resolved, unresolved = panel.quotable(workspace, skill), {}, [], []
    for fid in mine:
        entry = results[fid] if isinstance(results[fid], dict) else {}
        claim, evidence, regression = entry.get("resolved"), entry.get("evidence"), entry.get("regression")
        if not isinstance(claim, bool):
            raise GateError(f"{fid}: `resolved` must be true or false")
        if regression is not None and not (isinstance(regression, str) and regression.strip()):
            raise GateError(f"{fid}: `regression` is null or the failure the fix introduced in the touched lines")
        if claim and not (isinstance(evidence, str) and evidence.strip()):
            raise GateError(f"{fid}: a resolved finding cites the fixed line as `evidence` (a verbatim quote)")
        if isinstance(evidence, str) and evidence.strip():
            panel._verify_quote(fid, evidence, texts)
        ok = claim and regression is None
        (resolved if ok else unresolved).append(fid)
        out[fid] = {"resolved": ok, "claimed": claim, "evidence": evidence if isinstance(evidence, str) else "",
                    "regression": regression}
    sha = skill_sha256(workspace, skill)
    path = cycle_dir(workspace, cycle["n"]) / f"{role}.json"
    write_json(path, {"role": role, "cycle": cycle["n"], "skill": skill, "skill_sha256": sha,
                      "rubric_sha256": panel.read_rubric(workspace).sha256, "commit": status.commit,
                      "results": out, "written": now()})
    log_attempt(workspace, "recheck", role=role, cycle=cycle["n"], skill_sha256=sha)
    return path, resolved, unresolved


# The ship rule after a cycle --------------------------------------------------------------------------------------

@dataclass
class CycleRule:
    n: int
    allowed: str
    gate: str
    metric: str
    consensus_line: str
    resolved: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # the last cycle's
    panel_cap: str | None = None  # the panel-verdict path's cap from its original label
    unscored: bool = False
    held: list[str] = field(default_factory=list)  # ids still holding the verdict at UNSHIPPED

    def cycle_line(self) -> str:
        return (f"FIX CYCLE {self.n} of {max_cycles()}: resolved {', '.join(self.resolved) or 'none'}; "
                f"unresolved {', '.join(self.unresolved) or 'none'}")

    def line(self) -> str:
        cap = f"; panel caps at {self.panel_cap}" if self.panel_cap else ""
        return (f"SHIP RULE: {self.allowed} (fix cycle {self.n}{cap}); gate: {self.gate}; metric: {self.metric}; "
                f"veto: {', '.join(self.held) or 'none'}")


def rule(workspace: Path, gate, skill_sha: str) -> CycleRule | None:
    """The verdict the build may carry once its latest cycle closed on this skill (None otherwise), never above
    the original consensus: the lower of the gate's level, the metric's and (panel-verdict path) the label's cap,
    SHIP WITH CAVEATS for ITERATE, BLOCK or ESCALATE; UNSHIPPED while a blocking finding is unresolved or a
    re-check names a regression (standing)."""
    import check_gate  # noqa: PLC0415
    found = cycles(workspace)
    if not found or closed(workspace, found[-1]) != skill_sha:
        return None
    first, last = found[0], found[-1]
    consensus, why = panel.recorded(workspace, gate.skill, first["reviewed_sha"])
    if consensus is None:
        raise GateError(f"the panel's consensus on the reviewed skill no longer validates: {why}")
    resolved = [fid for c in found for fid in outcome(workspace, c)[0]]
    _, unresolved = outcome(workspace, last)
    _, held = standing(workspace, found)
    gate_cap, gate_text = check_gate.gate_level(gate)
    value, how = check_gate.ship_metric(workspace, gate.skill)
    target = consensus.target
    metric_text = f"{how} -> {value:.3f} target {target:.2f}" if value is not None else f"none ({how})"
    panel_cap = None if consensus.checkable else (
        consensus.allowed if consensus.label not in UNSHIPPING else "SHIP WITH CAVEATS")
    levels = [gate_cap, _metric_level(workspace, value)] + ([panel_cap] if panel_cap else []) + (
        ["UNSHIPPED"] if held else [])
    return CycleRule(last["n"], min(levels, key=panel.RANK.__getitem__), gate_text, metric_text, consensus.line(),
                     resolved, unresolved, panel_cap, value is None, held)


if __name__ == "__main__":
    import sys

    USAGE = ("fix_cycle.py is a library, not a command: check_gate.py runs it.\n\n"
             "    check_gate.py --next                            # names the fix cycle's next step\n"
             "    check_gate.py --fix-cycle [--finding ID ...]    # open the cycle --next (or the freeze guard) offers\n"
             "    check_gate.py --recheck-receipt ROLE [JSON]     # each re-checking role, after it checks\n\n")
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(USAGE + __doc__.strip())
        sys.exit(0)
    sys.stderr.write(USAGE)
    sys.exit(2)
