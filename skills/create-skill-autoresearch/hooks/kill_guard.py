#!/usr/bin/env python3
"""Shell-command hook: block pattern-based process kills, on every host whose hooks can block a command.

A name-pattern kill (`pkill -f gate`, `killall python3`, `pgrep -f x | xargs kill`, `kill $(pgrep x)`,
`taskkill /IM python.exe`, `Stop-Process -Name node`) also hits sibling builds and the user's own
processes; the factory's run_owned.py stops only the job it recorded. One script, two input/output
contracts (references/hosts.md):

  Claude Code, Codex  PreToolUse, stdin {"tool_name": ..., "tool_input": {"command": ...}}.
                      Block: exit 2, reason on stderr. Allow: exit 0, no output.
  Cursor              beforeShellExecution, stdin {"hook_event_name": "beforeShellExecution",
                      "command": ..., "cwd": ..., "sandbox": ...}. Always answers JSON on stdout,
                      exit 0: {"permission": "deny", "user_message", "agent_message"} or
                      {"permission": "allow"} (Cursor blocks a permission hook that prints nothing).

hooks/kill-guard runs it with whichever Python 3 the machine has; OpenCode runs it through
hooks/opencode-kill-guard.js. Input it cannot read is let through, with {"permission": "allow"} on
stdout for Cursor's sake: the guard must never stop every shell call. A command that only mentions one
of these words (a grep for `pkill`) is blocked too; the message says what to run instead.

Detection runs on the whole command with line breaks flattened and quotes and backslashes removed (and,
separately, with backslashes as separators, for a Windows path such as C:\\Windows\\System32\\taskkill.exe),
so `p'k'ill`, `"pkill"`, `\\pkill`, line continuations and a pipe or `$( … )` split across lines do not hide
a kill; the Windows patterns also ignore case and PowerShell's backtick and cmd's caret escapes. Blocked:
  pkill, killall                              anywhere as a word (any path: /usr/bin/pkill, pkill.exe)
  pgrep | pidof | ps ... | ... kill           a process listing piped into kill
  kill ... $(pgrep ...), `pgrep ...` ... kill a process listing substituted into a command that kills
                                              (`pids=$(pgrep x)` then `kill $pids` included)
  taskkill /IM, taskkill /FI                  taskkill by image name or filter (taskkill /PID <n> is allowed)
  Stop-Process -Name, spps -Name, kill -Name  PowerShell kill by name (Stop-Process -Id <n> is allowed)
  Get-Process | tasklist | Get-CimInstance |  a process listing and a kill in one command: piped,
    Get-WmiObject ... with Stop-Process,      $_.Kill(), or a variable passed on
    .Kill(), taskkill or Terminate
  wmic process ... delete | call terminate    wmic kill by query
"""

from __future__ import annotations

import json
import re
import sys

MESSAGE = ("Blocked by create-skill-autoresearch's kill guard. Use scripts/run_owned.py stop <name>; "
           "pattern kills hit unrelated processes. To stop one known process, kill its PID "
           "(kill <pid>, taskkill /PID <pid>, Stop-Process -Id <pid>).")
_LISTERS = r"(?:pgrep|pidof|ps)"
NAMED_KILL = re.compile(r"\b(?:pkill|killall)\b")
PIPED_KILL = re.compile(rf"\b{_LISTERS}\b.*\|.*\bkill\b")
SUBSTITUTED_LISTING = re.compile(rf"(?:\$\(|`)\s*{_LISTERS}\b")
KILL = re.compile(r"\bkill\b")
_WIN_LISTERS = r"\b(?:get-process|gps|tasklist|get-ciminstance|gcim|get-wmiobject|gwmi)\b"
_WIN_KILLERS = r"(?:\b(?:stop-process|spps|taskkill|invoke-cimmethod|remove-ciminstance)\b|\.kill\s*\(|\bterminate\b)"
WINDOWS_KILLS = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in (
    r"taskkill(?:\.exe)?\b.*\s[/-](?:im|fi)\b",
    r"\b(?:stop-process|spps)\b.*\s-(?:n\w*|processname)\b",
    r"\bkill\b.*\s-(?:name|processname)\b",
    rf"{_WIN_LISTERS}.*{_WIN_KILLERS}",
    r"\bwmic(?:\.exe)?\b.*\bprocess\b.*\b(?:delete|terminate)\b",
)]
CURSOR_EVENT = "beforeShellExecution"
ALLOW = {"permission": "allow"}


def normalise(command: str) -> str:
    """Quotes and backslashes removed, every line break flattened to a space."""
    return re.sub(r"\s*[\r\n]+\s*", " ", re.sub(r"[\"'\\]", "", command.replace("\\\n", " ")))


def is_pattern_kill(command: str) -> bool:
    text = normalise(command)
    as_path = normalise(command.replace("\\\n", " ").replace("\\", "/"))
    if any(NAMED_KILL.search(t) or PIPED_KILL.search(t) or (KILL.search(t) and SUBSTITUTED_LISTING.search(t))
           for t in (text, as_path)):
        return True
    windows = (text, as_path, re.sub(r"[`^]", "", text))
    return any(pattern.search(t) for pattern in WINDOWS_KILLS for t in windows)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        event = None
    if not isinstance(event, dict):
        print(json.dumps(ALLOW))
        return 0
    if event.get("hook_event_name") == CURSOR_EVENT or ("command" in event and "tool_input" not in event):
        command = event.get("command")
        blocked = isinstance(command, str) and is_pattern_kill(command)
        reply = {"permission": "deny", "user_message": MESSAGE, "agent_message": MESSAGE} if blocked else ALLOW
        print(json.dumps(reply))
        return 0
    command = (event.get("tool_input") or {}).get("command")
    if isinstance(command, str) and is_pattern_kill(command):
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
