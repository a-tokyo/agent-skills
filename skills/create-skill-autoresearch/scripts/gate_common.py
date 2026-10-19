"""Shared helpers for gate.py and check_gate.py: workspace paths, the two YAML keys they read, and
the hashes that bind a gate run to the skill and cases it ran.

No PyYAML (the factory bundles stdlib-only scripts), so `read_data_split` is a deliberately small
reader for a few top-level keys of work/evaluation/data-split.yaml:

    plugin_eval_available: 0          # an integer; anything else is kept as a string
    plugin_eval: [case-a, "case-b"]   # a one-line flow list, or a block list:
    plugin_eval:
      - case-a
      - "case-b"

Case names are strings (quotes optional, `#` comments stripped). Any other shape for
`plugin_eval` (a mapping, a multi-line flow list, a scalar) is reported as an error rather
than guessed at. The split keys (`all_cases`, `training`, `validation`, `test`) are read the same
way: whether any holds a value tells check_gate.py the build has gold standards, and their entries
are the manual protocol's gate cases. `write_gate_cases` rewrites `plugin_eval` and
`plugin_eval_available` in place and leaves every other line as it was; `gate.py record` is its
only caller.

`tree_sha256` hashes a directory's sorted relative paths and file contents, skipping interpreter
caches (`__pycache__/`, `*.pyc`) and `.DS_Store`, which running a skill's scripts or browsing it in
Finder creates without changing what ships.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path("work") / "evaluation"
PLUGIN_ARTIFACT = EVAL_DIR / "plugin-eval.json"
MANUAL_ARTIFACT = EVAL_DIR / "uplift.json"
GATE_RECORD = EVAL_DIR / "gate.json"
DATA_SPLIT = EVAL_DIR / "data-split.yaml"
RUN_CASE = EVAL_DIR / "run-case.sh"
PLUGIN_DIR = EVAL_DIR / "plugin"
GATE_ATTEMPTS = EVAL_DIR / "gate-attempts.jsonl"
REQUIRED_RUNS = 3
# Hashed elsewhere (run-case.sh, data-split.yaml) or written by the gate itself.
_NOT_INPUTS = {p.relative_to(EVAL_DIR)
               for p in (PLUGIN_ARTIFACT, MANUAL_ARTIFACT, GATE_RECORD, DATA_SPLIT, RUN_CASE, GATE_ATTEMPTS)}
_SCRATCH_SUFFIXES = {".tmp", ".out", ".log"}

GOLD_KEYS = ("all_cases", "training", "validation", "test")
_GATE_KEYS = ("plugin_eval", "plugin_eval_available")
_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_INT = re.compile(r"^-?\d+$")


class GateError(Exception):
    """A precondition the gate cannot run or validate without; the message says what to fix."""


@dataclass
class DataSplit:
    plugin_eval: list[str] | None
    plugin_eval_error: str | None
    plugin_eval_available: int | str | None
    gold_recorded: bool = False
    gold: dict[str, list[str]] = field(default_factory=dict)
    gold_error: str | None = None

    def gate_gold(self) -> list[str]:
        """The gold standards the gate runs (SKILL.md 3.6): the test split when it lists any (10 or more
        gold standards), otherwise every gold standard across all_cases, training and validation."""
        if self.gold.get("test"):
            return list(self.gold["test"])
        seen: dict[str, None] = {}
        for key in ("all_cases", "training", "validation"):
            seen.update(dict.fromkeys(self.gold.get(key, [])))
        return list(seen)


def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(token: str) -> str:
    """A double-quoted token is decoded as JSON (the form write_gate_cases emits, so `\\u00e9` and other
    escapes round-trip); a single-quoted one has its quotes stripped; anything else is kept as written."""
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] == '"':
        try:
            decoded = json.loads(token)
        except json.JSONDecodeError:
            return token[1:-1]
        return decoded if isinstance(decoded, str) else token[1:-1]
    if len(token) >= 2 and token[0] == token[-1] == "'":
        return token[1:-1]
    return token


def _parse_list(value: str, block: list[str], key: str = "plugin_eval") -> tuple[list[str] | None, str | None]:
    if value.startswith("["):
        if not value.endswith("]"):
            return None, f"{key} flow list must open and close on one line"
        items = [_unquote(t) for t in value[1:-1].split(",")] if value[1:-1].strip() else []
    elif value.startswith("{"):
        return None, f"{key} is a mapping ({value!r}), not a list of case names"
    elif value:
        return None, f"{key} is a scalar ({value!r}), not a list of case names"
    else:
        items = []
        for raw in block:
            item = raw.strip()
            if not item.startswith("- "):
                return None, f"{key} must be a list of case names; found {item!r}"
            items.append(_unquote(item[2:]))
    if any(not item for item in items):
        return None, f"{key} has an empty case name"
    if len(set(items)) != len(items):
        return None, f"{key} lists a case twice"
    return items, None


def read_data_split(path: Path) -> DataSplit:
    if not path.is_file():
        raise GateError(f"{path} not found; 3.6 records the gate cases there under plugin_eval")
    lines = [_strip_comment(line) for line in path.read_text().splitlines()]
    found: dict[str, tuple[str, list[str]]] = {}
    for i, line in enumerate(lines):
        match = _TOP_KEY.match(line)
        if not match or match.group(1) not in _GATE_KEYS + GOLD_KEYS:
            continue
        block = []
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                continue
            if not nxt[0].isspace():
                break
            block.append(nxt)
        found[match.group(1)] = (match.group(2).strip(), block)

    cases, error = (None, "plugin_eval is not recorded")
    if "plugin_eval" in found:
        cases, error = _parse_list(*found["plugin_eval"])
    available: int | str | None = None
    if "plugin_eval_available" in found:
        raw = _unquote(found["plugin_eval_available"][0])
        available = int(raw) if _INT.match(raw) else raw
    recorded = any(value not in ("", "[]") or block for key, (value, block) in found.items() if key in GOLD_KEYS)
    gold, gold_error = {}, None
    for key in GOLD_KEYS:
        if key in found:
            items, err = _parse_list(*found[key], key=key)
            if err:
                gold_error = gold_error or err
            else:
                gold[key] = items
    return DataSplit(plugin_eval=cases, plugin_eval_error=error, plugin_eval_available=available,
                     gold_recorded=recorded, gold=gold, gold_error=gold_error)


def _block_end(lines: list[str], start: int) -> int:
    """Index after the key at `start` and its indented block (trailing blank lines stay outside)."""
    end = start + 1
    for i in range(start + 1, len(lines)):
        if not lines[i].strip():
            continue
        if not lines[i][0].isspace():
            break
        end = i + 1
    return end


def write_gate_cases(path: Path, cases: list[str], available: int | str, source: str) -> None:
    """Rewrite `plugin_eval` (as a block list) and `plugin_eval_available` in data-split.yaml, keeping
    every other line; append them when absent. Creates the file when it does not exist."""
    lines = path.read_text().splitlines() if path.is_file() else []
    why = ("exit code of `claude plugin eval --help`" if isinstance(available, int)
           else "user-invoked skill: always the manual protocol, claude not probed")
    new = {
        "plugin_eval_available": [f"plugin_eval_available: {available}  # {why}"],
        "plugin_eval": [f"plugin_eval:  # written by scripts/gate.py record from {source}; re-run it, never edit"]
                       + [f"  - {json.dumps(case)}" for case in cases],
    }
    for key, block in new.items():
        at = next((i for i, line in enumerate(lines)
                   if (m := _TOP_KEY.match(_strip_comment(line))) and m.group(1) == key), None)
        if at is None:
            lines += block
        else:
            lines[at:_block_end(lines, at)] = block
    write_text(path, "\n".join(lines) + "\n")


def resolve_skill(workspace: Path, skill: str | None) -> str:
    output = workspace / "output"
    if skill:
        if not (output / skill / "SKILL.md").is_file():
            raise GateError(f"no skill at {output / skill / 'SKILL.md'}")
        return skill
    found = sorted(p.parent.name for p in output.glob("*/SKILL.md")) if output.is_dir() else []
    if len(found) != 1:
        raise GateError(f"found {len(found)} skills under {output}; pass --skill <name>")
    return found[0]


def model_invoked(skill_md: Path) -> bool:
    """False when the frontmatter sets `disable-model-invocation: true`."""
    text = skill_md.read_text()
    if not text.startswith("---"):
        return True
    end = text.find("\n---", 3)
    frontmatter = text[3:end] if end != -1 else ""
    for line in frontmatter.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "disable-model-invocation":
            return _unquote(_strip_comment(value)).lower() != "true"
    return True


def _hashed(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return path.is_file() and "__pycache__" not in rel.parts and path.suffix != ".pyc" and path.name != ".DS_Store"


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    if root.is_dir():
        for path in sorted((p for p in root.rglob("*") if _hashed(p, root)), key=lambda p: p.relative_to(root).as_posix()):
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def skill_sha256(workspace: Path, skill: str) -> str:
    return tree_sha256(workspace / "output" / skill)


def cases_sha256(workspace: Path, cases: list[str]) -> str:
    """The recorded case names, in order, plus each case's suite directory under plugin/evals/ when it
    has one (manual-protocol gold cases may not)."""
    digest = hashlib.sha256()
    for case in cases:
        digest.update(case.encode() + b"\0")
        digest.update(tree_sha256(workspace / PLUGIN_DIR / "evals" / case).encode())
    return digest.hexdigest()


def file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _gate_input(path: Path, ws: Path) -> bool:
    rel = path.relative_to(ws)
    if rel.parts[:2] != EVAL_DIR.parts:
        return True
    inner = rel.relative_to(EVAL_DIR)
    return not (inner.parts[0] == PLUGIN_DIR.name or inner in _NOT_INPUTS or path.name.startswith(".")
                or path.suffix in _SCRATCH_SUFFIXES or path.name.endswith(".prev.json"))


def inputs_sha256(workspace: Path) -> dict[str, str]:
    """sha256 of every file the gate cases can resolve to outside the suite: the gold standards and
    fixtures under input/, and the scorers under work/evaluation/ (score scripts, rubrics, fixtures).
    Skipped there: plugin/ (the skill and case hashes cover it), run-case.sh (hashed on its own), what
    the gate writes, data-split.yaml (the case hash covers plugin_eval), dotfiles and *.tmp / *.out /
    *.log scratch."""
    found = {}
    for root in (workspace / "input", workspace / EVAL_DIR):
        if root.is_dir():
            for path in root.rglob("*"):
                if _hashed(path, root) and _gate_input(path, workspace):
                    found[path.relative_to(workspace).as_posix()] = file_sha256(path)
    return dict(sorted(found.items()))


def log_attempt(workspace: Path, step: str, error: str | None = None) -> None:
    """Append one line to work/evaluation/gate-attempts.jsonl: `record` or `gate`, and the GateError that
    stopped it, if any. check_gate.py --finish reads it: an UNSHIPPED build must have tried the gate."""
    path = workspace / GATE_ATTEMPTS
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step, "error": error}
    with open(path, "a") as fh:
        fh.write(json.dumps(line) + "\n")


def read_attempts(workspace: Path) -> list[dict]:
    path = workspace / GATE_ATTEMPTS
    if not path.is_file():
        return []
    attempts = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            attempts.append(entry)
    return attempts


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_json(path: Path, data: object) -> None:
    write_text(path, json.dumps(data, indent=2) + "\n")
