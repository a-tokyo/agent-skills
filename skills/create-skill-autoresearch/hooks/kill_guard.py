#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block pattern-based process kills in Bash tool calls.

A name-pattern kill (`pkill -f gate`, `killall python3`, `pgrep -f x | xargs kill`, `kill $(pgrep x)`)
also hits sibling builds and the user's own processes; the factory's run_owned.py stops only the
process group it recorded. Loaded by hooks/hooks.json when this skill directory is loaded as a
plugin (`claude --plugin-dir <skill-dir>`), and by the agent-skills marketplace entry.

Reads the hook's JSON input on stdin. Exit 2 with the reason on stderr blocks the call; exit 0
lets it through to the normal permission flow. Input it cannot read is let through: the guard
must never stop every Bash call. A command that only mentions one of these words (a grep for
`pkill`) is blocked too; the message says what to run instead.

Detection runs on the command with quotes and backslashes removed, so `p'k'ill`, `"pkill"`,
`\\pkill` and line continuations do not hide a kill. Blocked:
  pkill, killall                              anywhere as a word (any path: /usr/bin/pkill)
  pgrep | pidof | ps ... | ... kill           a process listing piped into kill on the same line
  kill ... $(pgrep ...), `pgrep ...` ... kill a process listing substituted into a line with kill
"""

from __future__ import annotations

import json
import re
import sys

MESSAGE = ("Blocked by create-skill-autoresearch's kill guard. Use scripts/run_owned.py stop <name>; "
           "pattern kills hit unrelated processes. To stop one known process, kill its PID.")
_LISTERS = r"(?:pgrep|pidof|ps)"
NAMED_KILL = re.compile(r"\b(?:pkill|killall)\b")
PIPED_KILL = re.compile(rf"\b{_LISTERS}\b[^\n]*\|[^\n]*\bkill\b")
SUBSTITUTED_LISTING = re.compile(rf"(?:\$\(|`)\s*{_LISTERS}\b")
KILL = re.compile(r"\bkill\b")


def normalise(command: str) -> str:
    return re.sub(r"[\"'\\]", "", command.replace("\\\n", " "))


def is_pattern_kill(command: str) -> bool:
    text = normalise(command)
    if NAMED_KILL.search(text) or PIPED_KILL.search(text):
        return True
    return any(KILL.search(line) and SUBSTITUTED_LISTING.search(line) for line in text.splitlines())


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command")
    if isinstance(command, str) and is_pattern_kill(command):
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
