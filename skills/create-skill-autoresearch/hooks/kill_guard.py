#!/usr/bin/env python3
"""Shell-command hook: block pattern-based process kills (`pkill`, `killall`, `fuser -k`, a process listing and a
kill in one call), which also hit sibling builds and the user's processes; run_owned.py stops only what it
recorded. What it blocks and allows, and each host's install: references/hosts.md "Kill guard".

  Claude Code, Codex  PreToolUse {"tool_input": {"command": ...}}: block = exit 2, reason on stderr; allow = exit 0.
  Cursor              beforeShellExecution {"command": ...}: always JSON on stdout, exit 0, {"permission":
                      "deny", "user_message", "agent_message"} or {"permission": "allow"}.

It acts only in a factory build (a session directory, or a builds/<name>/ under it, holding work/manifest.yaml or
work/evaluation/, the subagent guard's test) or an executor session (CSA_EXECUTOR set): anywhere else every call is
allowed, so installing the plugin changes nothing outside a build. Input it cannot read is allowed (with Cursor's
JSON): the guard must never stop every shell call. It is a best-effort tripwire for honest mistakes, not a sandbox:
it sees one call at a time.

The loop guard rides on the same hook: a call that runs a build's Phase 4 metric (`work/evaluation/evaluate.sh`
with no case argument, the autoresearch run step) is denied while `check_gate.py --loop` says the loop is done
(phase-4.md 4.3). A case argument (`evaluate.sh <case>`, the validation list) always runs, as does any call in an
executor session (CSA_EXECUTOR set) or one whose evaluate.sh path it cannot resolve (`$VAR`, unknown cwd).

The read guard's shell half rides on it too (hooks/read_guard.py): a `cat`, `sed`, `head`, ... of this skill's
script or hook source, or a second whole read of a factory reference in one context, is denied.

The freeze guard's shell half rides on it too (hooks/freeze_guard.py): once a panel round has its first receipt,
a redirect, `tee`, `cp`, `mv`, `rm`, `touch`, `sed -i`, ... writing a build's output/<skill>/ is denied unless a fix
cycle is open, with the command that opens one.

The poll guard rides on it too: while a job the build owns runs (a run_owned.py record in work/pids/: `gate`,
`rescore`, an evaluate run), a Bash sleep or poll loop (`sleep N`, `until ...; do`, `while ...; do ... sleep`,
`while kill -0`) and Claude Code's Monitor tool are denied in the build's main session, with the exact command
that blocks on that job instead (each poll re-reads the whole context). Otherwise, and in an executor session or a
subagent, they run.

Long commands block (Claude Code): a call it lets through that runs this factory's scripts (gate.py, rescore.py,
run_owned.py, check_gate.py) or a build's work/evaluation/*.sh gets `timeout` 600000 and `run_in_background` false
through `hookSpecificOutput.updatedInput` (exit 0, no permission decision), so the scripts' 270 s waits finish
inside one call instead of the call moving to the background after the 120 s default. Claude Code replaces the
whole tool input with updatedInput (toolExecution.ts `processedInput = result.updatedInput`, checked against the
2.1.x source and the 2.1.316 build), so the hook echoes every other field unchanged. A host that ignores it keeps
the call's own timeout; the scripts then exit 124 with "issue the same command again", which re-attaches.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

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
POLLS = [re.compile(r"(?<![\w-])sleep\s+\d"), re.compile(r"\bwhile\s+kill\s+-0\b"),
         re.compile(r"\buntil\b.*?\bdo\b", re.S), re.compile(r"\bwhile\b.*?\bdo\b.*?\bsleep\b", re.S)]
POLL_MESSAGE = ("Blocked by create-skill-autoresearch's poll guard: the build's `{name}` job is running ({ws}). "
                "Polling re-reads your whole context on every call; block on the job instead, in one foreground "
                "Bash call with timeout 600000:\n  {command}\nOn exit 124 (still running) issue that same command "
                "again.")
# This factory's scripts and a build's evaluation scripts: the calls that may wait up to 270 s.
LONG = re.compile(r"(?:^|[\s/;&|(`=])(?:gate|rescore|run_owned|check_gate)\.py(?![\w.-])"
                  r"|(?:work/evaluation/[\w.-]+|(?:^|[\s/;&|(`=])(?:evaluate|evaluate-checks|run-case))\.sh(?![\w.-])")
BLOCKING_MS = 600000  # Claude Code's longest Bash timeout (BASH_MAX_TIMEOUT_MS default)
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
        import check_gate  # noqa: E402  (the skill's own scripts, beside this hook)

        for ws in workspaces:
            done, reason = check_gate.loop_done(Path(ws).resolve())
            if done:
                return LOOP_MESSAGE.format(reason=reason)
    except Exception as exc:  # noqa: BLE001  (never stop every metric run on the guard's own failure)
        print(f"loop guard: {type(exc).__name__}: {exc}; command allowed", file=sys.stderr)
    return None


def workspaces(cwd: str | None) -> list[str]:
    """The factory builds at `cwd` (else this process's directory) or directly under its builds/."""
    root = cwd or os.getcwd()
    builds = os.path.join(root, "builds")
    candidates = [root, *(os.path.join(builds, n) for n in (sorted(os.listdir(builds)) if os.path.isdir(builds) else []))]
    return [ws for ws in candidates if os.path.isfile(os.path.join(ws, "work", "manifest.yaml"))
            or os.path.isdir(os.path.join(ws, "work", "evaluation"))]


def guarded(cwd: str | None) -> bool:
    """An executor session, or a factory build at `cwd` (else this process's directory) or under its builds/."""
    if os.environ.get("CSA_EXECUTOR"):
        return True
    try:
        return bool(workspaces(cwd))
    except OSError:
        return False


def running_job(cwd: str | None) -> tuple[str, str, str] | None:
    """(workspace, job name, the command that blocks on it) for the first owned job running in a build here; the
    gate's own eval runs (gate-*) are covered by `gate`."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, SCRIPTS)
    import run_owned  # noqa: E402  (the skill's own scripts, beside this hook)

    for ws in workspaces(cwd):
        sdir = os.path.join(ws, "work", "pids")
        names = sorted(n[:-5] for n in (os.listdir(sdir) if os.path.isdir(sdir) else []) if n.endswith(".json"))
        for name in (n for n in names if not n.startswith("gate-")):
            try:
                if run_owned.status(Path(sdir), name)["state"] != "running":
                    continue
            except run_owned.OwnedError:
                continue
            q, script = shlex.quote, (lambda s: q(os.path.join(SCRIPTS, s)))
            command = {"gate": f"python3 {script('gate.py')} --workspace {q(ws)} --wait",
                       "rescore": f"python3 {script('rescore.py')} --workspace {q(ws)}"}.get(
                name, f"python3 {script('run_owned.py')} --workspace {q(ws)} wait {q(name)}")
            return ws, name, command
    return None


def poll_block(command: str | None, cwd: str | None, event: dict | None) -> str | None:
    """The poll guard's deny message for a sleep or poll loop (command) or a Monitor call (None) while a build job
    runs, or None; never in an executor session or a subagent. Any error of its own allows the call."""
    if os.environ.get("CSA_EXECUTOR") or (event or {}).get("agent_id"):
        return None
    if command is not None and not any(p.search(normalise(command)) for p in POLLS):
        return None
    try:
        job = running_job(cwd)
    except Exception as exc:  # noqa: BLE001
        print(f"poll guard: {type(exc).__name__}: {exc}; call allowed", file=sys.stderr)
        return None
    return None if job is None else POLL_MESSAGE.format(ws=job[0], name=job[1], command=job[2])


def read_block(command: str, cwd: str | None, event: dict | None) -> str | None:
    """hooks/read_guard.py's deny message for a shell read of the factory's source or a reference already in
    context, or None. Any error of its own allows the call."""
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import read_guard  # noqa: E402  (beside this hook)

        return read_guard.shell_block(command, cwd, event)
    except Exception as exc:  # noqa: BLE001
        print(f"read guard: {type(exc).__name__}: {exc}; command allowed", file=sys.stderr)
        return None


def freeze_block(command: str, cwd: str | None) -> str | None:
    """hooks/freeze_guard.py's deny message for a shell write to a frozen output/<skill>/, or None. Any error of its
    own allows the call."""
    if "output" not in command:
        return None
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import freeze_guard  # noqa: E402  (beside this hook)

        return freeze_guard.shell_block(command, cwd)
    except Exception as exc:  # noqa: BLE001
        print(f"freeze guard: {type(exc).__name__}: {exc}; command allowed", file=sys.stderr)
        return None


def check(command: object, cwd: str | None, event: dict | None = None) -> str | None:
    """The deny reason for this call, or None: no guard acts outside a factory build."""
    if not isinstance(command, str) or not guarded(cwd):
        return None
    if is_pattern_kill(command):
        return MESSAGE
    return (loop_block(command, cwd) or poll_block(command, cwd, event) or read_block(command, cwd, event)
            or freeze_block(command, cwd))


def blocking_input(tool_input: dict, command: str, cwd: str | None) -> dict | None:
    """The Bash input with timeout 600000 and run_in_background false when the call runs a long factory command
    in a build (not an executor session), else None."""
    if os.environ.get("CSA_EXECUTOR") or not LONG.search(normalise(command)) or not guarded(cwd):
        return None
    if tool_input.get("timeout") == BLOCKING_MS and tool_input.get("run_in_background") is False:
        return None
    return {**tool_input, "timeout": BLOCKING_MS, "run_in_background": False}


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
        reason = check(command, cwd, event)
        reply = {"permission": "deny", "user_message": reason, "agent_message": reason} if reason else ALLOW
        print(json.dumps(reply))
        return 0
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if event.get("tool_name") == "Monitor":
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else None
        reason = poll_block(None, cwd, event) if guarded(cwd) else None
        if reason:
            print(reason, file=sys.stderr)
            return 2
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else None
    reason = check(command, cwd, event)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    try:
        updated = blocking_input(tool_input, command, cwd) if event.get("tool_name", "Bash") == "Bash" else None
    except Exception as exc:  # noqa: BLE001  (never stop a call on the rewrite's own failure)
        print(f"kill guard: {type(exc).__name__}: {exc}; input unchanged", file=sys.stderr)
        updated = None
    if updated is not None:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
