"""Shared helpers for gate.py and check_gate.py: workspace paths, the two YAML keys they read, and
the hashes that bind a gate run to the skill and cases it ran.

No PyYAML (the factory bundles stdlib-only scripts), so `read_data_split` is a deliberately small
reader for a few top-level keys of work/evaluation/data-split.yaml:

    plugin_eval_available: 0          # an integer; anything else is kept as a string
    plugin_eval: [case-a, "case-b"]   # a one-line flow list, or a block list:
    plugin_eval:
      - case-a
      - "case-b"

Case names are strings (quotes optional, `#` comments stripped; a flow list splits on commas outside
quotes, so `["a, b.md"]` is one name). Any other shape for
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

import glob
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

# POSIX-style relative paths, so messages and records read the same on Windows; `workspace / EVAL_DIR`
# still yields a native path.
EVAL_DIR = PurePosixPath("work") / "evaluation"
PLUGIN_ARTIFACT = EVAL_DIR / "plugin-eval.json"
MANUAL_ARTIFACT = EVAL_DIR / "uplift.json"
GATE_RECORD = EVAL_DIR / "gate.json"
DATA_SPLIT = EVAL_DIR / "data-split.yaml"
RUN_CASE = EVAL_DIR / "run-case.sh"
PLUGIN_DIR = EVAL_DIR / "plugin"
GATE_ATTEMPTS = EVAL_DIR / "gate-attempts.jsonl"
BASELINE_CACHE = EVAL_DIR / "baseline-cache"
REQUIRED_RUNS = 3
# Hashed elsewhere (run-case.sh, data-split.yaml) or written by the gate itself: its records, the skill
# copy it refreshes into the wrapper plugin before each run, what claude plugin eval writes there, and
# the manual protocol's cached without-skill arms (check_gate.py validates those against their keys).
_NOT_INPUTS = {p.as_posix() for p in (PLUGIN_ARTIFACT, MANUAL_ARTIFACT, GATE_RECORD, DATA_SPLIT, RUN_CASE,
                                      GATE_ATTEMPTS)}
_GENERATED_DIRS = tuple(f"{d.as_posix()}/" for d in (PLUGIN_DIR / "skills", PLUGIN_DIR / "evals" / "results",
                                                      BASELINE_CACHE))

GOLD_KEYS = ("all_cases", "training", "validation", "test")
# SKILL.md 4.2: from this many gold standards the split holds out a test set; below it, leave-one-out.
TEST_SPLIT_MIN = 10
_GATE_KEYS = ("plugin_eval", "plugin_eval_available")
_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_INT = re.compile(r"^-?\d+$")
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
BASELINE_KEY = re.compile(r"[0-9a-f]{64}")


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

    def all_gold(self) -> list[str]:
        """Every gold standard the split names, once, in key order."""
        seen: dict[str, None] = {}
        for key in GOLD_KEYS:
            seen.update(dict.fromkeys(self.gold.get(key, [])))
        return list(seen)

    def gate_gold(self) -> list[str]:
        """The gold standards the gate runs (SKILL.md 3.6, 4.2): the test split with 10 or more gold
        standards, every gold standard with 3-9. Raises GateError on a split that breaks that rule."""
        total, test = len(self.all_gold()), self.gold.get("test", [])
        if total >= TEST_SPLIT_MIN and not test:
            raise GateError(f"{total} gold standards and no test split; with {TEST_SPLIT_MIN} or more, 4.2 holds "
                            "out 10% under `test` and the gate runs those")
        if total < TEST_SPLIT_MIN and test:
            raise GateError(f"{total} gold standards and a test split of {len(test)}; with fewer than "
                            f"{TEST_SPLIT_MIN}, 4.2 has no test split and the gate runs every gold standard: "
                            "list the `test` entries under all_cases or training instead")
        return list(test) if test else self.all_gold()


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


def _flow_items(inner: str) -> list[str] | None:
    """The comma-separated items of a one-line flow list, splitting only outside quotes (a backslash escapes
    inside double quotes, as in JSON); None when a quote is left open."""
    items, buf, quote, escaped = [], [], None, False
    for ch in inner:
        if escaped:
            escaped = False
        elif quote == '"' and ch == "\\":
            escaped = True
        elif quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            items.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    items.append("".join(buf))
    return None if quote else [_unquote(t) for t in items]


def _parse_list(value: str, block: list[str], key: str = "plugin_eval") -> tuple[list[str] | None, str | None]:
    if value.startswith("["):
        if not value.endswith("]"):
            return None, f"{key} flow list must open and close on one line"
        items = _flow_items(value[1:-1]) if value[1:-1].strip() else []
        if items is None:
            return None, f"{key} flow list has an unclosed quote"
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
    lines = [_strip_comment(line) for line in path.read_text(encoding="utf-8").splitlines()]
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


def write_gate_cases(path: Path, cases: list[str], available: int | str, source: str, why: str) -> None:
    """Rewrite `plugin_eval` (as a block list) and `plugin_eval_available` (commented with `why`) in
    data-split.yaml, keeping every other line; append them when absent. Creates the file when absent."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
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


_MANIFEST_INPUT = re.compile(r"""(?:^|[\s{,-])input:\s*["']?([^"',}\s#]+)""")


def _case_file(ws: Path, entry: str) -> Path | None:
    """The file a gold-standard entry names: a path from the workspace root, or the one file under input/
    called <entry>.<ext>."""
    if (ws / entry).is_file():
        return (ws / entry).resolve()
    found = [p for p in (ws / "input").rglob(f"{glob.escape(entry)}.*") if p.is_file()] if (ws / "input").is_dir() else []
    return found[0].resolve() if len(found) == 1 else None


def omitted_gold(ws: Path, listed: list[str]) -> list[str]:
    """Gold standards on disk that the split's lists leave out: every `input:` path in work/manifest.yaml,
    and every file beside a listed case file that differs from it only in its numbers (listing
    input/input-01.md covers input/input-02.md, input/input-03.md, ...). Only entries that name a file
    are checked, so case names that are not files never trip it."""
    ws = ws.resolve()
    listed_files = {f for entry in listed if (f := _case_file(ws, entry))}
    if not listed_files:
        return []
    on_disk: set[Path] = set()
    for path in listed_files:
        pattern = re.compile(re.sub(r"\d+", r"\\d+", re.escape(path.name)))
        if pattern.pattern != re.escape(path.name):
            on_disk.update(p for p in path.parent.iterdir() if p.is_file() and pattern.fullmatch(p.name))
    manifest = ws / "work" / "manifest.yaml"
    if manifest.is_file():
        for match in _MANIFEST_INPUT.finditer(manifest.read_text(encoding="utf-8", errors="replace")):
            if (ws / match.group(1)).is_file():
                on_disk.add((ws / match.group(1)).resolve())
    return sorted(p.relative_to(ws).as_posix() for p in on_disk - listed_files)


def utf8_stdio() -> None:
    """Case names and claude's output may be non-ASCII; Windows pipes default to a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def check_skill_name(skill: object) -> str:
    """The SKILL.md 3.3 `name` rule, so a skill name can never be a path: a gate deletes and rewrites
    work/evaluation/plugin/skills/<name>/."""
    if not isinstance(skill, str) or len(skill) > 64 or not SKILL_NAME.fullmatch(skill):
        raise GateError(f"skill name {skill!r} is not a skill name (lowercase letters, digits and single hyphens, "
                        "<= 64 chars; a name under output/, never a path)")
    return skill


def resolve_skill(workspace: Path, skill: str | None) -> str:
    output = workspace / "output"
    if skill is not None:
        check_skill_name(skill)
        if not (output / skill / "SKILL.md").is_file():
            raise GateError(f"no skill at {output / skill / 'SKILL.md'}")
        return skill
    found = sorted(p.parent.name for p in output.glob("*/SKILL.md")) if output.is_dir() else []
    if len(found) != 1:
        raise GateError(f"found {len(found)} skills under {output}; pass --skill <name>")
    return check_skill_name(found[0])


def model_invoked(skill_md: Path) -> bool:
    """False when the frontmatter sets `disable-model-invocation: true`."""
    text = skill_md.read_text(encoding="utf-8")
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


def split_sha256(split: DataSplit) -> str:
    """The splits as parsed (all_cases, training, validation, test, or the error reading them): a change to
    which gold standard sits in which split after the gate is stale, while comments and layout are not."""
    canonical = {"gold": {key: split.gold.get(key) for key in GOLD_KEYS}, "error": split.gold_error}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _gate_input(path: Path, ws: Path) -> bool:
    rel = path.relative_to(ws).as_posix()
    if not rel.startswith(f"{EVAL_DIR.as_posix()}/"):
        return True
    return not (rel in _NOT_INPUTS or rel.startswith(_GENERATED_DIRS)
                or path.name.endswith((".tmp", ".prev.json")))


def inputs_sha256(workspace: Path) -> dict[str, str]:
    """sha256 of every file a gate case can resolve to: the gold standards and fixtures under input/, and
    everything under work/evaluation/ — scorers, rubrics, fixtures, golden files and the wrapper plugin
    (its manifest, shared mocks and case files). Skipped there: run-case.sh (hashed on its own),
    data-split.yaml (cases_sha256 covers plugin_eval, split_sha256 the splits), what the gate writes (gate.json, the artifacts,
    gate-attempts.jsonl, baseline-cache/, *.prev.json, *.tmp), plugin/skills/ (refreshed from output/<skill>/,
    which the skill hash covers) and plugin/evals/results/ (claude plugin eval's own output)."""
    found = {}
    for root in (workspace / "input", workspace / EVAL_DIR):
        if root.is_dir():
            for path in root.rglob("*"):
                if _hashed(path, root) and _gate_input(path, workspace):
                    found[path.relative_to(workspace).as_posix()] = file_sha256(path)
    return dict(sorted(found.items()))


def baseline_key(workspace: Path, case: str, model: str, executor: str, inputs: dict[str, str] | None = None) -> str:
    """sha256 of everything a manual-protocol without-skill run depends on: the case, the pinned model and
    executor, run-case.sh, the case's suite directory and every gate input (inputs_sha256: the gold
    standards, fixtures, scorers and graders). The skill is not in it: the bare arm never reads it."""
    canonical = {"case": case, "model": model, "executor": executor,
                 "run_case": file_sha256(workspace / RUN_CASE),
                 "suite": tree_sha256(workspace / PLUGIN_DIR / "evals" / case),
                 "inputs": inputs_sha256(workspace) if inputs is None else inputs}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def baseline_path(workspace: Path, key: str) -> Path:
    if not BASELINE_KEY.fullmatch(key):
        raise GateError(f"baseline key {key!r} is not a sha256")
    return workspace / BASELINE_CACHE / f"{key}.json"


def read_baseline(workspace: Path, key: str, runs: int) -> list[float] | None:
    """The without-skill scores cached under `key` when the entry is well formed, names that key and holds
    exactly `runs` scores in 0..1; else None."""
    try:
        entry = json.loads(baseline_path(workspace, key).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    scores = entry.get("runs") if isinstance(entry, dict) else None
    if (not isinstance(entry, dict) or entry.get("key") != key or not isinstance(scores, list) or len(scores) != runs
            or not all(isinstance(s, (int, float)) and not isinstance(s, bool) and 0 <= s <= 1 for s in scores)):
        return None
    return [float(s) for s in scores]


def log_attempt(workspace: Path, step: str, error: str | None = None) -> None:
    """Append one line to work/evaluation/gate-attempts.jsonl: `record` or `gate`, and the GateError that
    stopped it, if any. check_gate.py --finish reads it: an UNSHIPPED build must have tried the gate."""
    path = workspace / GATE_ATTEMPTS
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step, "error": error}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def read_attempts(workspace: Path) -> list[dict]:
    path = workspace / GATE_ATTEMPTS
    if not path.is_file():
        return []
    attempts = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            attempts.append(entry)
    return attempts


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows: a reader holding path open blocks the replace for a moment (50 x 20 ms: a second).
            if os.name != "nt":
                raise
            time.sleep(0.02)
    os.replace(tmp, path)


def write_json(path: Path, data: object) -> None:
    write_text(path, json.dumps(data, indent=2) + "\n")
