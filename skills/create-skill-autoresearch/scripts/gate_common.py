"""Shared helpers for gate.py, check_gate.py and panel.py: workspace paths, a small stdlib reader and writer for
the data-split.yaml keys they use, and the hashes that bind a gate run to what it ran.

read_data_split reads only top-level keys, each a one-line flow list (`[a, "b, c.md"]`, split on commas outside
quotes) or a block list of `- item` lines; quotes are optional and `#` comments stripped. Any other shape is
reported as an error, never guessed at. write_gate_cases rewrites plugin_eval and plugin_eval_available in place
and keeps every other line. Hashes skip `__pycache__/`, `*.pyc` and `.DS_Store`, which running or browsing a
skill creates without changing what ships.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

if os.name == "nt":
    sys.exit("native Windows is not supported; run the factory inside WSL")

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
# Not gate inputs: hashed on their own (run-case.sh, data-split.yaml), or written by the gate and claude plugin
# eval (the records, the skill copy, eval results, the cached without-skill arms check_gate.py re-keys).
_NOT_INPUTS = {p.as_posix() for p in (PLUGIN_ARTIFACT, MANUAL_ARTIFACT, GATE_RECORD, DATA_SPLIT, RUN_CASE,
                                      GATE_ATTEMPTS)}
_GENERATED_DIRS = tuple(f"{d.as_posix()}/" for d in (PLUGIN_DIR / "skills", PLUGIN_DIR / "evals" / "results",
                                                      BASELINE_CACHE))
GOLD_KEYS = ("all_cases", "training", "validation", "test")
TEST_SPLIT_MIN = 10  # phase-4.md 4.2: from this many gold standards a test set is held out
_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
BASELINE_KEY = re.compile(r"[0-9a-f]{64}")
_MANIFEST_INPUT = re.compile(r"""(?:^|[\s{,-])input:\s*["']?([^"',}\s#]+)""")


class GateError(Exception):
    """A precondition the gate cannot run or validate without; the message says what to fix."""


@dataclass
class DataSplit:
    plugin_eval: list[str] | None
    plugin_eval_error: str | None
    plugin_eval_available: int | str | None
    gold: dict[str, list[str]] = field(default_factory=dict)
    gold_error: str | None = None

    @property
    def gold_recorded(self) -> bool:
        """Whether the split names any gold standard (check_gate.py then needs a gold gate case)."""
        return bool(self.all_gold()) or self.gold_error is not None

    def all_gold(self) -> list[str]:
        """Every gold standard the split names, once, in key order."""
        return list(dict.fromkeys(e for key in GOLD_KEYS for e in self.gold.get(key, [])))

    def gate_gold(self) -> list[str]:
        """The gold standards the gate runs (phase-3.md 3.6, phase-4.md 4.2): the test split with 10 or more gold
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
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(token: str) -> str:
    """A double-quoted token decodes as JSON (as write_gate_cases emits it), a single-quoted one loses its quotes;
    anything else is kept as written."""
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
    """The comma-separated items of a one-line flow list, split only outside quotes (a backslash escapes inside
    double quotes); None when a quote is left open."""
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


def _parse_list(value: str, block: list[str], key: str) -> tuple[list[str] | None, str | None]:
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
        for item in (raw.strip() for raw in block):
            if not item.startswith("- "):
                return None, f"{key} must be a list of case names; found {item!r}"
            items.append(_unquote(item[2:]))
    if any(not item for item in items):
        return None, f"{key} has an empty case name"
    if len(set(items)) != len(items):
        return None, f"{key} lists a case twice"
    return items, None


def _top_keys(lines: list[str]) -> dict[str, tuple[int, int, str, list[str]]]:
    """Each top-level key: (its line, the index after its indented block, its inline value, the block's
    non-blank lines). `lines` are comment-stripped; trailing blank lines stay outside the block."""
    found = {}
    for i, line in enumerate(lines):
        if match := _TOP_KEY.match(line):
            end, block = i + 1, []
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    if not lines[j][0].isspace():
                        break
                    end = j + 1
                    block.append(lines[j])
            found.setdefault(match.group(1), (i, end, match.group(2).strip(), block))
    return found


def read_data_split(path: Path) -> DataSplit:
    if not path.is_file():
        raise GateError(f"{path} not found; 3.6 records the gate cases there under plugin_eval")
    found = _top_keys([_strip_comment(line) for line in path.read_text(encoding="utf-8").splitlines()])
    cases, error = _parse_list(*found["plugin_eval"][2:], "plugin_eval") if "plugin_eval" in found else (
        None, "plugin_eval is not recorded")
    available: int | str | None = None
    if "plugin_eval_available" in found:
        raw = _unquote(found["plugin_eval_available"][2])
        available = int(raw) if re.fullmatch(r"-?\d+", raw) else raw
    split = DataSplit(plugin_eval=cases, plugin_eval_error=error, plugin_eval_available=available)
    for key in (k for k in GOLD_KEYS if k in found):
        items, err = _parse_list(*found[key][2:], key)
        if err:
            split.gold_error = split.gold_error or err
        else:
            split.gold[key] = items
    return split


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
        at = _top_keys([_strip_comment(line) for line in lines]).get(key)
        if at is None:
            lines += block
        else:
            lines[at[0]:at[1]] = block
    write_text(path, "\n".join(lines) + "\n")


def _case_file(ws: Path, entry: str) -> Path | None:
    """The file a gold-standard entry names: a path from the workspace root, or the one file under input/
    called <entry>.<ext>."""
    if (ws / entry).is_file():
        return (ws / entry).resolve()
    found = [p for p in (ws / "input").rglob(f"{glob.escape(entry)}.*") if p.is_file()] if (ws / "input").is_dir() else []
    return found[0].resolve() if len(found) == 1 else None


def omitted_gold(ws: Path, listed: list[str]) -> list[str]:
    """Gold standards on disk that the split's lists leave out: every `input:` path in work/manifest.yaml, and
    every file beside a listed case file that differs from it only in its numbers (input/input-01.md covers
    input/input-02.md, ...). Only entries that name a file are checked."""
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
    """Case names and claude's output may be non-ASCII, whatever the locale's encoding."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def check_skill_name(skill: object) -> str:
    """The phase-3.md 3.3 `name` rule, so a skill name can never be a path: a gate deletes and rewrites
    work/evaluation/plugin/skills/<name>/."""
    if not isinstance(skill, str) or len(skill) > 64 or not SKILL_NAME.fullmatch(skill):
        raise GateError(f"skill name {skill!r} is not a skill name (lowercase letters, digits and single hyphens, "
                        "<= 64 chars; a name under output/, never a path)")
    return skill


def resolve_skill(workspace: Path, skill: str | None) -> str:
    output = workspace / "output"
    if skill is not None:
        if not (output / check_skill_name(skill) / "SKILL.md").is_file():
            raise GateError(f"no skill at {output / skill / 'SKILL.md'}")
        return skill
    found = sorted(p.parent.name for p in output.glob("*/SKILL.md")) if output.is_dir() else []
    if len(found) != 1:
        raise GateError(f"found {len(found)} skills under {output}; pass --skill <name>")
    return check_skill_name(found[0])


def model_invoked(skill_md: Path) -> bool:
    """False when the frontmatter sets `disable-model-invocation: true`."""
    text = skill_md.read_text(encoding="utf-8")
    end = text.find("\n---", 3) if text.startswith("---") else -1
    for line in text[3:end].splitlines() if end != -1 else []:
        key, _, value = line.partition(":")
        if key.strip() == "disable-model-invocation":
            return _unquote(_strip_comment(value)).lower() != "true"
    return True


def _hashed(path: Path, root: Path) -> bool:
    return (path.is_file() and "__pycache__" not in path.relative_to(root).parts and path.suffix != ".pyc"
            and path.name != ".DS_Store")


def tree_sha256(root: Path) -> str:
    """A directory's sorted relative paths and file contents."""
    digest = hashlib.sha256()
    if root.is_dir():
        for path in sorted((p for p in root.rglob("*") if _hashed(p, root)), key=lambda p: p.relative_to(root).as_posix()):
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def skill_sha256(workspace: Path, skill: str) -> str:
    return tree_sha256(workspace / "output" / skill)


def cases_sha256(workspace: Path, cases: list[str]) -> str:
    """The recorded case names, in order, plus each case's suite directory under plugin/evals/ when it has one
    (manual-protocol gold cases may not)."""
    digest = hashlib.sha256()
    for case in cases:
        digest.update(case.encode() + b"\0")
        digest.update(tree_sha256(workspace / PLUGIN_DIR / "evals" / case).encode())
    return digest.hexdigest()


def split_sha256(split: DataSplit) -> str:
    """The splits as parsed: moving a gold standard between splits after the gate makes it stale; comments and
    layout do not."""
    canonical = {"gold": {key: split.gold.get(key) for key in GOLD_KEYS}, "error": split.gold_error}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read_json(path: Path) -> dict | None:
    """The JSON object in `path`, or None when it is missing, unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def json_lines(path: Path | None) -> list[dict]:
    """The JSON objects on the lines of a .jsonl file; other lines, or no file, are skipped."""
    found = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if path and path.is_file() else []:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            found.append(entry)
    return found


def file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def inputs_sha256(workspace: Path) -> dict[str, str]:
    """sha256 of every file a gate case can resolve to: input/ and work/evaluation/ (scorers, rubrics, fixtures,
    golden files, the wrapper plugin), less what _NOT_INPUTS and _GENERATED_DIRS name and *.prev.json, *.tmp."""
    found = {}
    for root in (workspace / "input", workspace / EVAL_DIR):
        for path in root.rglob("*") if root.is_dir() else []:
            rel = path.relative_to(workspace).as_posix()
            generated = rel.startswith(f"{EVAL_DIR.as_posix()}/") and (
                rel in _NOT_INPUTS or rel.startswith(_GENERATED_DIRS) or path.name.endswith((".tmp", ".prev.json")))
            if _hashed(path, root) and not generated:
                found[rel] = file_sha256(path)
    return dict(sorted(found.items()))


def baseline_key(workspace: Path, case: str, model: str, executor: str, inputs: dict[str, str] | None = None) -> str:
    """sha256 of everything a manual without-skill run depends on: the case, the pinned model and executor,
    run-case.sh, the case's suite directory and every gate input. Not the skill: the bare arm never reads it."""
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
    """The without-skill scores cached under `key` when the entry names that key and holds exactly `runs` scores
    in 0..1; else None."""
    entry = read_json(baseline_path(workspace, key)) or {}
    scores = entry.get("runs")
    if (entry.get("key") != key or not isinstance(scores, list) or len(scores) != runs
            or not all(is_number(s) and 0 <= s <= 1 for s in scores)):
        return None
    return [float(s) for s in scores]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_attempt(workspace: Path, step: str, error: str | None = None) -> None:
    """Append `record` or `gate`, and the GateError that stopped it, to gate-attempts.jsonl: check_gate.py
    --finish requires an UNSHIPPED build to have tried the gate."""
    path = workspace / GATE_ATTEMPTS
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now(), "step": step, "error": error}) + "\n")


def read_attempts(workspace: Path) -> list[dict]:
    return json_lines(workspace / GATE_ATTEMPTS)


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path: Path, data: object) -> None:
    write_text(path, json.dumps(data, indent=2) + "\n")
