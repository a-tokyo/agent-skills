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
`docker ps | grep x; kill 123` and `ps aux | head; kill $PID` pass, except when a later kill reads what
the listing filled: a variable assigned from it (`pids=$(pgrep x)`, `for p in $(pgrep x)`, a `read`,
`mapfile` or `readarray` name, PowerShell `$p = Get-Process x`) or a file it was written to (`pgrep x > f`,
`| tee f`). `kill` counts only as a word of its own, never inside `kill-guard`/`kill_guard`. The Windows
patterns also ignore case and PowerShell's backtick and cmd's caret escapes. Blocked:
  pkill, killall                              anywhere as a word (any path: /usr/bin/pkill, pkill.exe)
  pgrep | pidof | ps ... | ... kill           a process listing piped into kill
  kill ... $(pgrep ...), `pgrep ...`, <(…)    a process listing substituted into a command that kills,
                                              wrappers included (`$(command pgrep x)`, `$(sudo pgrep x)`)
  pids=$(pgrep x); kill $pids                 a listing kept in a variable (or a for/while-read loop) then
                                              killed by that variable
  pgrep x > f; kill $(cat f)                  a listing kept in a file, then read by a kill
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
# `kill` as a command word: not `kill-guard` or `kill_guard` (this hook's own files).
_KILL = r"(?<![\w-])kill(?![\w-])"
NAMED_KILL = re.compile(r"\b(?:pkill|killall)\b")
PIPED_KILL = re.compile(rf"{_LISTERS}.*\|.*{_KILL}")
# A listing inside $( … ), ` … ` or <( … ), wrappers (`command`, `sudo`, `env X=1`) and all.
SUBSTITUTED_LISTING = re.compile(rf"(?:\$\(|<\(|`)[^)`]*{_LISTERS}")
LISTING = re.compile(rf"{SUBSTITUTED_LISTING.pattern}|{_LISTERS}.*\|")
KILL = re.compile(_KILL)
# Variables a listing fills: `pids=$(pgrep x)`, `for p in $(pgrep x)`, and (when the command lists
# processes anywhere) the names a `read`, `mapfile` or `readarray` fills.
ASSIGNED = re.compile(r"\b(\w+)=(?:\$\(|`)([^)`]*)")
FOR_LISTING = re.compile(rf"\bfor\s+(\w+)\s+in\b.*{_LISTERS}")
READ_VARS = re.compile(r"\bread\b((?:\s+-\w+)*(?:\s+[A-Za-z_]\w*)+)")
ARRAY_FILL = re.compile(r"\b(?:mapfile|readarray)\b[^<]*?(\w+)\s*<")
# A listing written to a file (`pgrep x > /tmp/p`, `ps … | tee p`) and later read by a kill.
LISTING_FILE = re.compile(rf"{_LISTERS}.*?(?:(?<![\d&>])>>?(?!&)|\btee\b(?:\s+-\w+)*)\s*([^\s;&|<>()]+)")
_WIN_LISTERS = r"\b(?:get-process|gps|tasklist|get-ciminstance|gcim|get-wmiobject|gwmi)\b"
_WIN_KILLERS = (rf"(?:\b(?:stop-process|spps|taskkill|invoke-cimmethod|remove-ciminstance)\b|{_KILL}"
                r"|\.kill\s*\(|\bterminate\b)")
WINDOWS_KILLS = [re.compile(p, re.IGNORECASE) for p in (
    r"taskkill(?:\.exe)?\b.*\s[/-](?:im|fi)\b",
    r"\b(?:stop-process|spps)\b.*\s-(?:n\w*|processname)\b",
    r"\bkill\b.*\s-(?:name|processname)\b",
    rf"(?=.*{_WIN_LISTERS}).*{_WIN_KILLERS}",
    r"\bwmic(?:\.exe)?\b.*\bprocess\b.*\b(?:delete|terminate)\b",
)]
WIN_LISTING_ASSIGNED = re.compile(rf"\$(\w+)\s*=.*{_WIN_LISTERS}", re.IGNORECASE)
WIN_KILLER = re.compile(_WIN_KILLERS, re.IGNORECASE)
CURSOR_EVENT = "beforeShellExecution"
ALLOW = {"permission": "allow"}


def normalise(command: str) -> str:
    """Quotes, line continuations and backslashes removed; line breaks kept (they separate commands). Bash
    deletes a backslash-newline pair outright, so `pki\\<newline>ll` is `pkill`: the halves are joined."""
    return re.sub(r"[\"'\\]", "", re.sub(r"\\\r?\n", "", command))


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


def _refers(command: str, names: set[str], flags: int = 0) -> bool:
    return any(re.search(rf"\$\{{?{re.escape(n)}\b", command, flags) for n in names)


def _sh_kill(commands: list[str]) -> bool:
    if any(NAMED_KILL.search(c) or PIPED_KILL.search(c) or (KILL.search(c) and SUBSTITUTED_LISTING.search(c))
           for c in commands):
        return True
    files = {m.group(1) for c in commands for m in LISTING_FILE.finditer(c) if m.group(1) != "/dev/null"}
    listed = {m.group(1) for c in commands for m in FOR_LISTING.finditer(c)}
    for c in commands:
        for name, body in ASSIGNED.findall(c):
            if re.search(_LISTERS, body) or any(f in body for f in files):
                listed.add(name)
    if files or any(LISTING.search(c) for c in commands):
        listed |= {n for c in commands for m in READ_VARS.finditer(c) for n in m.group(1).split() if n[0] != "-"}
        listed |= {m.group(1) for c in commands for m in ARRAY_FILL.finditer(c)}
    kills = [c for c in commands if KILL.search(c)]
    return any(_refers(c, listed) or any(f in c for f in files) for c in kills)


def _windows_kill(commands: list[str]) -> bool:
    if any(p.search(c) for p in WINDOWS_KILLS for c in commands):
        return True
    listed = {m.group(1) for c in commands for m in WIN_LISTING_ASSIGNED.finditer(c)}
    return any(WIN_KILLER.search(c) and _refers(c, listed, re.IGNORECASE) for c in commands)


def is_pattern_kill(command: str) -> bool:
    text = normalise(command)
    as_path = normalise(re.sub(r"\\\r?\n", "", command).replace("\\", "/"))
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
