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

Quotes, backslashes and line continuations are removed first (and, separately, backslashes become
separators, for a Windows path such as C:\\Windows\\System32\\taskkill.exe), so `p'k'ill`, `"pkill"` and
`\\pkill` do not hide a kill. The command is then split into simple commands at `;`, `&&`, `||`, `&` and
line breaks outside $( … ), ( … ), { … } and backticks; a pipeline, or a pipe or substitution continued on
the next line, stays one command. The kill and its listing must share a simple command, so
`docker ps | grep x; kill 123` passes, except when the listing is kept in a variable that a later command
kills. The Windows patterns also ignore case and PowerShell's backtick and cmd's caret escapes. Blocked:
  pkill, killall                              anywhere as a word (any path: /usr/bin/pkill, pkill.exe)
  pgrep | pidof | ps ... | ... kill           a process listing piped into kill
  kill ... $(pgrep ...), `pgrep ...`, <(…)    a process listing substituted into a command that kills,
                                              wrappers included (`$(command pgrep x)`, `$(sudo pgrep x)`)
  pids=$(pgrep x); kill $pids                 a listing kept in a variable (or a for/while-read loop) then
                                              killed by variable
  taskkill /IM, taskkill /FI                  taskkill by image name or filter (taskkill /PID <n> is allowed)
  Stop-Process -Name, spps -Name, kill -Name  PowerShell kill by name (Stop-Process -Id <n> is allowed)
  Get-Process | tasklist | Get-CimInstance |  a process listing and a kill in one command (piped, $_.Kill(),
    Get-WmiObject ... with Stop-Process, spps, or PowerShell's `kill` alias), or a listing kept in a
    kill, .Kill(), taskkill or Terminate      variable that a later command kills
  wmic process ... delete | call terminate    wmic kill by query
"""

from __future__ import annotations

import json
import re
import sys

MESSAGE = ("Blocked by create-skill-autoresearch's kill guard. Use scripts/run_owned.py stop <name>; "
           "pattern kills hit unrelated processes. To stop one known process, kill its PID "
           "(kill <pid>, taskkill /PID <pid>, Stop-Process -Id <pid>).")
_LISTERS = r"\b(?:pgrep|pidof|ps)\b"
NAMED_KILL = re.compile(r"\b(?:pkill|killall)\b")
PIPED_KILL = re.compile(rf"{_LISTERS}.*\|.*\bkill\b")
# A listing inside $( … ), ` … ` or <( … ), wrappers (`command`, `sudo`, `env X=1`) and all.
SUBSTITUTED_LISTING = re.compile(rf"(?:\$\(|<\(|`)[^)`]*{_LISTERS}")
LISTING = re.compile(rf"{SUBSTITUTED_LISTING.pattern}|{_LISTERS}.*\|")
KILL = re.compile(r"\bkill\b")
KILL_VARIABLE = re.compile(r"\bkill\b[^|]*\$\{?\w")
_WIN_LISTERS = r"\b(?:get-process|gps|tasklist|get-ciminstance|gcim|get-wmiobject|gwmi)\b"
_WIN_KILLERS = (r"(?:\b(?:stop-process|spps|kill|taskkill|invoke-cimmethod|remove-ciminstance)\b"
                r"|\.kill\s*\(|\bterminate\b)")
WINDOWS_KILLS = [re.compile(p, re.IGNORECASE) for p in (
    r"taskkill(?:\.exe)?\b.*\s[/-](?:im|fi)\b",
    r"\b(?:stop-process|spps)\b.*\s-(?:n\w*|processname)\b",
    r"\bkill\b.*\s-(?:name|processname)\b",
    rf"(?=.*{_WIN_LISTERS}).*{_WIN_KILLERS}",
    r"\bwmic(?:\.exe)?\b.*\bprocess\b.*\b(?:delete|terminate)\b",
)]
WIN_LISTING_ASSIGNED = re.compile(rf"\$\w+\s*=.*{_WIN_LISTERS}", re.IGNORECASE)
WIN_KILL_VARIABLE = re.compile(rf"(?=.*\$\w).*{_WIN_KILLERS}", re.IGNORECASE)
CURSOR_EVENT = "beforeShellExecution"
ALLOW = {"permission": "allow"}


def normalise(command: str) -> str:
    """Quotes, line continuations and backslashes removed; line breaks kept (they separate commands)."""
    return re.sub(r"[\"'\\]", "", re.sub(r"\\\r?\n", " ", command))


def simple_commands(text: str) -> list[str]:
    """Split on `;`, `&&`, `||`, `&` and line breaks outside $( … ), ( … ), { … } and ` … `; a pipeline
    stays one command, and a line break after `|` or inside a substitution continues it."""
    parts, buf, depth, tick = [], [], 0, False
    i = 0
    while i < len(text):
        ch, pair = text[i], text[i:i + 2]
        if ch == "`":
            tick = not tick
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth = max(0, depth - 1)
        elif depth == 0 and not tick:
            prev, nxt = text[i - 1:i], text[i + 1:i + 2]
            if pair in ("&&", "||"):
                parts.append("".join(buf))
                buf, i = [], i + 2
                continue
            if ch == ";" or (ch == "&" and prev not in "<>" and nxt != ">"):
                parts.append("".join(buf))
                buf, i = [], i + 1
                continue
            if ch in "\r\n":
                if "".join(buf).rstrip().endswith("|"):
                    ch = " "
                else:
                    parts.append("".join(buf))
                    buf, i = [], i + 1
                    continue
        buf.append(" " if ch in "\r\n" else ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _sh_kill(commands: list[str]) -> bool:
    if any(NAMED_KILL.search(c) or PIPED_KILL.search(c) or (KILL.search(c) and SUBSTITUTED_LISTING.search(c))
           for c in commands):
        return True
    # A listing kept in a variable, then killed: `pids=$(pgrep x); kill $pids`, `for p in $(pgrep x)`.
    return any(LISTING.search(c) for c in commands) and any(KILL_VARIABLE.search(c) for c in commands)


def _windows_kill(commands: list[str]) -> bool:
    if any(p.search(c) for p in WINDOWS_KILLS for c in commands):
        return True
    return (any(WIN_LISTING_ASSIGNED.search(c) for c in commands)
            and any(WIN_KILL_VARIABLE.search(c) for c in commands))


def is_pattern_kill(command: str) -> bool:
    text = normalise(command)
    as_path = normalise(re.sub(r"\\\r?\n", " ", command).replace("\\", "/"))
    if any(_sh_kill(simple_commands(t)) for t in (text, as_path)):
        return True
    return any(_windows_kill(simple_commands(t)) for t in (text, as_path, re.sub(r"[`^]", "", text)))


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
