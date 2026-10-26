#!/usr/bin/env python3
"""Shell-command hook: block pattern-based process kills (`pkill`, `killall`, `fuser -k`, a process listing and a
kill in one call), which also hit sibling builds and the user's processes; run_owned.py stops only what it
recorded. What it blocks and allows, and each host's install: references/hosts.md "Kill guard".

  Claude Code, Codex  PreToolUse {"tool_input": {"command": ...}}: block = exit 2, reason on stderr; allow = exit 0.
  Cursor              beforeShellExecution {"command": ...}: always JSON on stdout, exit 0, {"permission":
                      "deny", "user_message", "agent_message"} or {"permission": "allow"}.

Input it cannot read is allowed (with Cursor's JSON): the guard must never stop every shell call. It is a
best-effort tripwire for honest mistakes, not a sandbox: it sees one call at a time.

The loop guard rides on the same hook: a call that runs a build's Phase 4 metric (`work/evaluation/evaluate.sh`
with no case argument, the autoresearch run step) is denied while `check_gate.py --loop` says the loop is done
(phase-4.md 4.3). A case argument (`evaluate.sh <case>`, the validation list) always runs, as does any call in an
executor session (CSA_EXECUTOR set) or one whose evaluate.sh path it cannot resolve (`$VAR`, unknown cwd).
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
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
EVALUATE = re.compile(r"(?:[^\s;&|()`<>=]*/)?evaluate\.sh(?![\w.-])")
# What may precede evaluate.sh in its command for the call to run it: assignments and runner words.
RUNNER = re.compile(r"(?:(?:\w+=\S*|sudo|command|exec|env|nice|nohup|time|timeout|bash|sh|zsh|-\S+|\d+[smhd]?)\s+)*")
REDIRECT = re.compile(r"\d*(?:>>?|<<-?|<)&?\s*(?:\d+\b|[^\s;&|)]+)?")
CD = re.compile(r"(?:^|[;&|(\n])\s*cd\s+(\S+)")
LOOP_MESSAGE = ("Blocked by create-skill-autoresearch's loop guard: Phase 4 is done ({reason}); go to 5.0 (3.6 and "
                "4.6 first where they apply). `check_gate.py --loop` shows this; `evaluate.sh <case>` still runs.")


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


def metric_workspaces(command: str, cwd: str | None) -> list[str]:
    """The build workspaces whose Phase 4 metric this call runs: evaluate.sh in command position with no case
    argument, resolved against the last `cd` before it, else `cwd`, to <ws>/work/evaluation/evaluate.sh."""
    text, found = normalise(command), []
    for m in EVALUATE.finditer(text):
        start = max(text.rfind(c, 0, m.start()) for c in ";&|(`\n") + 1
        if not RUNNER.fullmatch(text[start:m.start()].lstrip()):
            continue  # written, read or copied, not run
        ends = [i for i in (text.find(c, m.end()) for c in ";&|)`\n") if i >= 0]
        if REDIRECT.sub(" ", text[m.end():min(ends, default=len(text))]).strip():
            continue  # a case argument: one case, not the metric
        cds = [c.group(1) for c in CD.finditer(text[:m.start()])]
        base = os.path.join(cwd or "", os.path.expanduser(cds[-1])) if cds else cwd
        path = os.path.expanduser(m.group(0))
        if "$" in path or "$" in (base or "") or not (os.path.isabs(path) or base):
            continue
        path = os.path.normpath(os.path.join(base or "", path))
        evaluation = os.path.dirname(path)
        if os.path.basename(evaluation) == "evaluation" and os.path.basename(os.path.dirname(evaluation)) == "work":
            found.append(os.path.dirname(os.path.dirname(evaluation)))
    return found


def loop_block(command: str, cwd: str | None) -> str | None:
    """The loop guard's deny message for this call, or None. Any error of its own allows the call."""
    if "evaluate.sh" not in command or os.environ.get("CSA_EXECUTOR"):
        return None
    try:
        workspaces = [ws for ws in metric_workspaces(command, cwd) if os.path.isfile(
            os.path.join(ws, "work", "evaluation", "evaluate.sh")) and os.path.isdir(os.path.join(ws, "output"))]
        if not workspaces:
            return None
        sys.dont_write_bytecode = True
        sys.path.insert(0, SCRIPTS)
        from pathlib import Path
        import check_gate  # noqa: E402  (the skill's own scripts, beside this hook)

        for ws in workspaces:
            done, reason = check_gate.loop_done(Path(ws).resolve())
            if done:
                return LOOP_MESSAGE.format(reason=reason)
    except Exception as exc:  # noqa: BLE001  (never stop every metric run on the guard's own failure)
        print(f"loop guard: {type(exc).__name__}: {exc}; command allowed", file=sys.stderr)
    return None


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
        roots = [r for r in event.get("workspace_roots") or [] if isinstance(r, str)]
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else (roots[0] if roots else None)
        reason = None if not isinstance(command, str) else MESSAGE if is_pattern_kill(command) else \
            loop_block(command, cwd)
        reply = {"permission": "deny", "user_message": reason, "agent_message": reason} if reason else ALLOW
        print(json.dumps(reply))
        return 0
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else None
    reason = MESSAGE if is_pattern_kill(command) else loop_block(command, cwd)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
