"""Shared helpers for gate.py and check_gate.py: workspace paths and the two YAML keys they read.

No PyYAML (the factory bundles stdlib-only scripts), so `read_data_split` is a deliberately small
reader for exactly two top-level keys of work/evaluation/data-split.yaml:

    plugin_eval_available: 0          # an integer; anything else is kept as a string
    plugin_eval: [case-a, "case-b"]   # a one-line flow list, or a block list:
    plugin_eval:
      - case-a
      - "case-b"

Case names are strings (quotes optional, `#` comments stripped). Any other shape for
`plugin_eval` (a mapping, a multi-line flow list, a scalar) is reported as an error rather
than guessed at.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

EVAL_DIR = Path("work") / "evaluation"
PLUGIN_ARTIFACT = EVAL_DIR / "plugin-eval.json"
MANUAL_ARTIFACT = EVAL_DIR / "uplift.json"
GATE_RECORD = EVAL_DIR / "gate.json"
DATA_SPLIT = EVAL_DIR / "data-split.yaml"
RUN_CASE = EVAL_DIR / "run-case.sh"
PLUGIN_DIR = EVAL_DIR / "plugin"
REQUIRED_RUNS = 3

_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_INT = re.compile(r"^-?\d+$")


class GateError(Exception):
    """A precondition the gate cannot run or validate without; the message says what to fix."""


@dataclass
class DataSplit:
    plugin_eval: list[str] | None
    plugin_eval_error: str | None
    plugin_eval_available: int | str | None


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
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _parse_list(value: str, block: list[str]) -> tuple[list[str] | None, str | None]:
    if value.startswith("["):
        if not value.endswith("]"):
            return None, "plugin_eval flow list must open and close on one line"
        items = [_unquote(t) for t in value[1:-1].split(",")] if value[1:-1].strip() else []
    elif value.startswith("{"):
        return None, f"plugin_eval is a mapping ({value!r}), not a list of case names"
    elif value:
        return None, f"plugin_eval is a scalar ({value!r}), not a list of case names"
    else:
        items = []
        for raw in block:
            item = raw.strip()
            if not item.startswith("- "):
                return None, f"plugin_eval must be a list of case names; found {item!r}"
            items.append(_unquote(item[2:]))
    if any(not item for item in items):
        return None, "plugin_eval has an empty case name"
    if len(set(items)) != len(items):
        return None, "plugin_eval lists a case twice"
    return items, None


def read_data_split(path: Path) -> DataSplit:
    if not path.is_file():
        raise GateError(f"{path} not found; 3.6 records the gate cases there under plugin_eval")
    lines = [_strip_comment(line) for line in path.read_text().splitlines()]
    found: dict[str, tuple[str, list[str]]] = {}
    for i, line in enumerate(lines):
        match = _TOP_KEY.match(line)
        if not match or match.group(1) not in ("plugin_eval", "plugin_eval_available"):
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
    return DataSplit(plugin_eval=cases, plugin_eval_error=error, plugin_eval_available=available)


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


def write_json(path: Path, data: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
