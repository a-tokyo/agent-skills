#!/usr/bin/env python3
"""Shell-command hook: block pattern-based process kills (`pkill`, `killall`, `fuser -k`, a process listing and a
kill in one call), which also hit sibling builds and the user's processes; run_owned.py stops only what it
recorded. What it blocks and allows, and each host's install: references/hosts.md "Kill guard".

  Claude Code, Codex  PreToolUse {"tool_input": {"command": ...}}: block = exit 2, reason on stderr; allow = exit 0.
  Cursor              beforeShellExecution {"command": ...}: always JSON on stdout, exit 0, {"permission":
                      "deny", "user_message", "agent_message"} or {"permission": "allow"}.

Input it cannot read is allowed (with Cursor's JSON): the guard must never stop every shell call. It is a
best-effort tripwire for honest mistakes, not a sandbox: it sees one call at a time.
"""

from __future__ import annotations

import json
import os
import re
import sys

if os.name == "nt":  # the host gets its "allow" answer, so no shell call or turn is held
    print("kill guard: native Windows is not supported; run the factory inside WSL", file=sys.stderr)
    print(json.dumps({"permission": "allow"}))
    sys.exit(0)

MESSAGE = ("Blocked by create-skill-autoresearch's kill guard. Use scripts/run_owned.py stop <name>; "
           "pattern kills hit unrelated processes. To stop one known process, kill its PID "
           "(kill <pid>) in a call that lists no processes.")
NAME_KILLS = [re.compile(r"\b(?:pkill|killall)\b"), re.compile(r"\bfuser\b.*\s-\w*k")]
LISTER = re.compile(r"\b(?:pgrep|pidof|lsof|ps)\b")
# `kill` as a command word: not `kill-guard` or `kill_guard` (this hook's own files).
KILLER = re.compile(r"(?<![\w-])kill(?![\w-])")
KEPT = re.compile(r"\$\(|[<>]\(|`|>(?!&\d|\s*/dev/null\b)|\btee\b|\$\w+\s*=")
WRAPPERS = re.compile(r"^(?:(?:sudo|command|builtin|exec)\s+)*")
KILL_WORD = re.compile(r"(?:\S*/)?kill")
LITERAL_ARG = re.compile(r"-?\d+(?:,\d+)*|%[\w%+-]*|--|-[A-Za-z]+\d*|[A-Z]+\d*")
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


def _literal_kill(segment: str) -> bool:
    words = WRAPPERS.sub("", segment.strip()).split()
    return bool(words) and bool(KILL_WORD.fullmatch(words[0])) and all(LITERAL_ARG.fullmatch(w) for w in words[1:])


def _listing_and_kill(text: str) -> bool:
    if not (LISTER.search(text) and KILLER.search(text)):
        return False
    if KEPT.search(text):
        return True
    return any(KILLER.search(segment) and (i > 0 or not _literal_kill(segment))
               for command in simple_commands(text) for i, segment in enumerate(command.split("|")))


def is_pattern_kill(command: str) -> bool:
    text = normalise(command)
    return any(p.search(c) for c in simple_commands(text) for p in NAME_KILLS) or _listing_and_kill(text)


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
