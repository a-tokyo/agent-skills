"""Fix cycles after the panel (SKILL.md 5.4, references/fix-cycle.md), for check_gate.py, gate.py and panel.py.

When the panel's consensus would end the build UNSHIPPED on cited, reproducible findings, the build may fix exactly
those findings instead of ending, up to `max_fix_cycles` cycles (references/verdict-rules.json `fix_cycle`):

  - a finding is a receipt's `veto` (`<role>.veto`; the only kind on the metric-first path) or, on the panel-verdict
    path, a failure scenario the receipt cites with a verbatim `evidence` quote and a `repro` (`<role>.F<i>`);
  - `check_gate.py --fix-cycle` opens cycle n: fix-cycles/<n>/cycle.json records the findings, the base commit (the
    skill the findings are against) and the gate-input hashes; work/experiments/fix-cycle-<n>.md gets a section
    per finding id for the fix notes and the lines the fix touches (`lines: <path>:<first>-<last>`);
  - the fix is ONE commit on top of the base, touching only declared lines (fix_status); gate.py re-gates only
    then (gate_refusal), rescore.py scores it, and the gate inputs must be the cycle's;
  - each role that raised a finding re-checks only its own findings on the fixed skill and writes a re-check
    receipt bound to the fixed skill's hash (`check_gate.py --recheck-receipt ROLE JSON`, write_recheck);
  - the ship rule (rule) keeps the original panel scores and clears the resolved findings: metric-first, the gate
    and the metric decide with the unresolved findings as the veto; on the panel-verdict path an ITERATE, BLOCK or
    ESCALATE label caps the verdict at SHIP WITH CAVEATS (the scores stay below target), the gate and metric below.
    An unresolved finding keeps the veto: another cycle on what is left while cycles remain, else UNSHIPPED.

A fix cycle never re-runs the panel or adds dimensions; outside a cycle a skill change after the panel still ends
the build UNSHIPPED (panel.exhausted).
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


def findings(receipts: dict[str, dict], checkable: bool) -> list[dict]:
    """The findings a fix cycle may fix: every receipt's veto, plus (panel-verdict path) its cited scenarios."""
    out = []
    for role in panel.ALL_ROLES:
        receipt = receipts.get(role)
        if not receipt:
            continue
        if veto := receipt.get("veto"):
            out.append({"id": f"{role}.veto", "role": role, "kind": veto["kind"], "evidence": veto["evidence"],
                        "repro": veto["repro"], "scenario": receipt.get("escalate") or ""})
        for found in [] if checkable else receipt.get("findings") or []:
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


def offer(workspace: Path, skill: str) -> Offer | None:
    """The next fix cycle the build may open, or None: cycle 1 when the panel's consensus ends the build UNSHIPPED
    on cited findings that fixing could clear (the gate and metric levels are not UNSHIPPED already); cycle n+1
    when cycle n closed on the skill as it is now with findings unresolved; never past max_fix_cycles."""
    import check_gate  # noqa: PLC0415  (check_gate imports this module)
    cap, found, current = max_cycles(), cycles(workspace), skill_sha256(workspace, skill)
    if found:
        last = found[-1]
        _, unresolved = outcome(workspace, last)
        left = [f for f in last["findings"] if f["id"] in unresolved]
        if closed(workspace, last) != current or last["n"] >= cap or not left:
            return None
        gate = check_gate.validate(workspace, skill)
        value, _ = check_gate.ship_metric(workspace, skill)
        if not gate.valid or "UNSHIPPED" in (check_gate.gate_level(gate)[0], _metric_level(workspace, value)):
            return None
        return Offer(last["n"] + 1, last["reviewed_sha"], current, left,
                     f"cycle {last['n']} left {', '.join(unresolved)} unresolved")
    if cap < 1:
        return None
    data = read_json(workspace / panel.CONSENSUS)
    reviewed = data.get("skill_sha256") if data else None
    if not isinstance(reviewed, str) or data.get("skill") != skill:
        return None
    consensus, _ = panel.recorded(workspace, skill, reviewed)
    if consensus is None or not (consensus.vetoes if consensus.checkable else consensus.label in UNSHIPPING):
        return None
    if not consensus.checkable and not (consensus.label == "ESCALATE" or check_gate._spent(workspace)
                                        or consensus.panel_round >= consensus.max_panel_rounds):
        return None  # the panel-verdict path still has its Phase 4 return (5.4 ITERATE)
    gate = check_gate.validate(workspace, skill, expect_sha=reviewed)
    if not gate.valid or check_gate.gate_level(gate)[0] == "UNSHIPPED":
        return None
    if consensus.checkable and current == reviewed:
        value, _ = check_gate.ship_metric(workspace, skill)
        if value is None or _metric_level(workspace, value) == "UNSHIPPED":
            return None  # unscored: rescore.py first; below target: fixing the veto cannot ship it
    receipts = panel.current_receipts(workspace, reviewed, panel.read_rubric(workspace).sha256,
                                      synthesis=bool(consensus.disputed))
    if not (listed := findings(receipts, consensus.checkable)):
        return None
    what = f"veto {', '.join(consensus.vetoes)}" if consensus.checkable else f"consensus {consensus.label}"
    return Offer(1, reviewed, reviewed, listed, f"the panel's {what} rests on cited findings")


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
    """The newest commit holding output/<skill>/ at `sha`: HEAD, else a commit the experiment log names."""
    import check_gate  # noqa: PLC0415
    _, experiments, _ = check_gate._experiments(workspace)
    seen: list[str] = []
    for ref in ["HEAD", *reversed([e["commit"].strip() for e in experiments if isinstance(e.get("commit"), str)])]:
        rev = _git(workspace, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        commit = rev.stdout.strip() if rev is not None and rev.returncode == 0 else ""
        if commit and commit not in seen:
            seen.append(commit)
            if commit_skill_sha(workspace, commit, skill) == sha:
                return commit
    return None


def open_cycle(workspace: Path, skill: str) -> dict:
    """Open the cycle offer() allows: write its record and the notes skeleton (kept if the builder wrote one)."""
    found = offer(workspace, skill)
    if found is None:
        raise GateError("no fix cycle to open: it needs a panel consensus or cycle that ends the build UNSHIPPED on "
                        f"cited findings, a gate and metric that are not UNSHIPPED, and fewer than {max_cycles()} "
                        "cycles (max_fix_cycles); run check_gate.py --next")
    base = _base_commit(workspace, skill, found.base_sha)
    if base is None:
        raise GateError(f"no commit holds output/{skill}/ as the panel (or the last cycle) saw it; the fix is one "
                        "commit on top of that version: commit the reviewed skill, or restore it and commit it")
    roles = [role for role in panel.ALL_ROLES if any(f["role"] == role for f in found.findings)]
    record = {"n": found.n, "skill": skill, "reviewed_sha": found.reviewed_sha, "base_sha": found.base_sha,
              "base_commit": base, "findings": found.findings, "roles": roles, "inputs": _inputs(workspace),
              "opened": now()}
    folder = cycle_dir(workspace, found.n)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "cycle.json", record)
    notes = notes_path(workspace, found.n)
    if not notes.is_file():
        notes.parent.mkdir(parents=True, exist_ok=True)
        sections = "".join(f"\n## {f['id']}\n\nFinding: {f['evidence']} (repro: {f['repro']})\nFix: \nlines: \n"
                           for f in found.findings)
        write_text(notes, f"# Fix cycle {found.n}\n\nFix only what each finding cites. `lines:` lists every line the "
                          f"fix commit touches, as `<path under output/{skill}/>:<first>-<last>` (new-file line "
                          "numbers; comma-separated; a file the commit adds or deletes: its path alone).\n" + sections)
    log_attempt(workspace, "fix_cycle", cycle=found.n, skill=skill, findings=[f["id"] for f in found.findings])
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
    unresolved: list[str] = field(default_factory=list)
    panel_cap: str | None = None  # the panel-verdict path's cap from its original label
    unscored: bool = False

    def cycle_line(self) -> str:
        return (f"FIX CYCLE {self.n} of {max_cycles()}: resolved {', '.join(self.resolved) or 'none'}; "
                f"unresolved {', '.join(self.unresolved) or 'none'}")

    def line(self) -> str:
        cap = f"; panel caps at {self.panel_cap}" if self.panel_cap else ""
        return (f"SHIP RULE: {self.allowed} (fix cycle {self.n}{cap}); gate: {self.gate}; metric: {self.metric}; "
                f"veto: {', '.join(self.unresolved) or 'none'}")


def rule(workspace: Path, gate, skill_sha: str) -> CycleRule | None:
    """The verdict the build may carry once its latest cycle closed on this skill (None otherwise): the original
    consensus with every resolved finding cleared; the lower of the gate's level, the metric's and (panel-verdict
    path) SHIP WITH CAVEATS; UNSHIPPED while a finding is unresolved."""
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
    gate_cap, gate_text = check_gate.gate_level(gate)
    value, how = check_gate.ship_metric(workspace, gate.skill)
    target = consensus.target
    metric_text = f"{how} -> {value:.3f} target {target:.2f}" if value is not None else f"none ({how})"
    panel_cap = None if consensus.checkable else (
        consensus.allowed if consensus.label not in UNSHIPPING else "SHIP WITH CAVEATS")
    levels = [gate_cap, _metric_level(workspace, value)] + ([panel_cap] if panel_cap else []) + (
        ["UNSHIPPED"] if unresolved else [])
    return CycleRule(last["n"], min(levels, key=panel.RANK.__getitem__), gate_text, metric_text, consensus.line(),
                     resolved, unresolved, panel_cap, value is None)


if __name__ == "__main__":
    import sys

    USAGE = ("fix_cycle.py is a library, not a command: check_gate.py runs it.\n\n"
             "    check_gate.py --next                            # names the fix cycle's next step\n"
             "    check_gate.py --fix-cycle                       # open the cycle --next offers\n"
             "    check_gate.py --recheck-receipt ROLE JSON       # each re-checking role, after it checks\n\n")
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(USAGE + __doc__.strip())
        sys.exit(0)
    sys.stderr.write(USAGE)
    sys.exit(2)
