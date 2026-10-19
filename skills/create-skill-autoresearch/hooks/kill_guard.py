#!/usr/bin/env python3
"""Shell-command hook: block pattern-based process kills, on every host whose hooks can block a command.

A name-pattern kill (`pkill -f gate`, `killall python3`, `pgrep -f x | xargs kill`, `kill $(pgrep x)`)
also hits sibling builds and the user's own processes; the factory's run_owned.py stops only the
process group it recorded. One script, two input/output contracts (references/hosts.md):

  Claude Code, Codex  PreToolUse, stdin {"tool_name": ..., "tool_input": {"command": ...}}.
                      Block: exit 2, reason on stderr. Allow: exit 0, no output.
  Cursor              beforeShellExecution, stdin {"hook_event_name": "beforeShellExecution",
                      "command": ..., "cwd": ..., "sandbox": ...}. Always answers JSON on stdout,
                      exit 0: {"permission": "deny", "user_message", "agent_message"} or
                      {"permission": "allow"} (Cursor blocks a permission hook that prints nothing).

OpenCode runs it through hooks/opencode-kill-guard.js. Input it cannot read is let through: the guard
must never stop every shell call. A command that only mentions one of these words (a grep for `pkill`)
is blocked too; the message says what to run instead.

Detection runs on the whole command with quotes and backslashes removed and line breaks flattened, so
`p'k'ill`, `"pkill"`, `\\pkill`, line continuations and a pipe or `$( … )` split across lines do not
hide a kill. Blocked:
  pkill, killall                              anywhere as a word (any path: /usr/bin/pkill)
  pgrep | pidof | ps ... | ... kill           a process listing piped into kill
  kill ... $(pgrep ...), `pgrep ...` ... kill a process listing substituted into a command that kills
                                              (`pids=$(pgrep x)` then `kill $pids` included)
"""

from __future__ import annotations

import json
import re
import sys

MESSAGE = ("Blocked by create-skill-autoresearch's kill guard. Use scripts/run_owned.py stop <name>; "
           "pattern kills hit unrelated processes. To stop one known process, kill its PID.")
_LISTERS = r"(?:pgrep|pidof|ps)"
NAMED_KILL = re.compile(r"\b(?:pkill|killall)\b")
PIPED_KILL = re.compile(rf"\b{_LISTERS}\b.*\|.*\bkill\b")
SUBSTITUTED_LISTING = re.compile(rf"(?:\$\(|`)\s*{_LISTERS}\b")
KILL = re.compile(r"\bkill\b")
CURSOR_EVENT = "beforeShellExecution"


def normalise(command: str) -> str:
    """Quotes and backslashes removed, every line break flattened to a space."""
    return re.sub(r"\s*[\r\n]+\s*", " ", re.sub(r"[\"'\\]", "", command.replace("\\\n", " ")))


def is_pattern_kill(command: str) -> bool:
    text = normalise(command)
    return bool(NAMED_KILL.search(text) or PIPED_KILL.search(text)
                or (KILL.search(text) and SUBSTITUTED_LISTING.search(text)))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(event, dict):
        return 0
    if event.get("hook_event_name") == CURSOR_EVENT or ("command" in event and "tool_input" not in event):
        command = event.get("command")
        blocked = isinstance(command, str) and is_pattern_kill(command)
        reply = ({"permission": "deny", "user_message": MESSAGE, "agent_message": MESSAGE} if blocked
                 else {"permission": "allow"})
        print(json.dumps(reply))
        return 0
    command = (event.get("tool_input") or {}).get("command")
    if isinstance(command, str) and is_pattern_kill(command):
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
