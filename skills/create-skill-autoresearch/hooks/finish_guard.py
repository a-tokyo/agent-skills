#!/usr/bin/env python3
"""End-of-turn hook: a factory build does not end before `check_gate.py --finish` exits 0 (SKILL.md 5.1).

In each build workspace (holding work/evaluation/ and output/<skill>/SKILL.md) at the session's directory or
under its builds/, an unfinished build sends the main session back once with the validator's reason, logged
to work/experiments/finish-guard.jsonl. Never twice in a row, never for a subagent's stop, and any input it
cannot read or error of its own ends the turn. Hosts and loop guards: references/hosts.md "Finish guard".

  Claude Code, Codex  Stop {"cwd", "stop_hook_active"}: {"decision": "block", "reason"} or {}.
  Cursor              stop {"status", "loop_count", "workspace_roots"}: {"followup_message"} or {}.

With --subagent it is the subagent guard instead (hosts.md "Subagent guard"): in a build past Phase 1
(work/manifest.yaml or work/evaluation/), a subagent dispatched in the background is denied.

  Claude Code  PreToolUse Agent {"tool_input": {"run_in_background"}}: absent or true denies (exit 2, reason
               on stderr); legacy Task denies only true.
  Cursor       preToolUse Task: run_in_background true denies; always JSON {"permission", ...}.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
if os.name == "nt":  # the host gets its "allow" answer, so no shell call or turn is held
    print("finish guard: native Windows is not supported; run the factory inside WSL", file=sys.stderr)
    print('{"permission": "allow"}' if "--subagent" in sys.argv[1:] else "{}")
    sys.exit(0)
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CURSOR_EVENT = "stop"
CURSOR_PRE_TOOL = "preToolUse"
ALLOW = {"permission": "allow"}
FOREGROUND = ("Blocked by create-skill-autoresearch's subagent guard: dispatch phase workers and panel roles in the "
              "foreground (run_in_background: false) and wait for them; for parallel roles, put several Agent calls "
              "in one message. A headless session stops background subagents 10 minutes after its last turn, "
              "which abandons the build.")
SUBAGENT_EVENTS = {"SubagentStop", "subagentStop"}
LOG = Path("work") / "experiments" / "finish-guard.jsonl"


def builds(root: Path, started: bool = False) -> list[Path]:
    """The factory build workspaces at `root` or directly under root/builds/: from Phase 3 on (work/evaluation/
    and output/<skill>/SKILL.md), or with `started` from Phase 1's manifest on."""
    candidates = [root, *sorted(p for p in (root / "builds").glob("*") if p.is_dir())]
    if started:
        return [ws for ws in candidates if (ws / "work" / "manifest.yaml").is_file() or (ws / "work" / "evaluation").is_dir()]
    return [ws for ws in candidates if (ws / "work" / "evaluation").is_dir() and any(ws.glob("output/*/SKILL.md"))]


def unfinished(roots: list[Path]) -> list[tuple[Path, str]]:
    sys.path.insert(0, str(SCRIPTS))
    import check_gate  # noqa: E402  (the skill's own scripts, beside this hook)

    out = []
    for root in roots:
        for ws in builds(root):
            done, _, reason = check_gate.finish(ws.resolve())
            if not done:
                out.append((ws, reason))
    return out


def message(found: list[tuple[Path, str]]) -> str:
    finish = f"python3 {SCRIPTS / 'check_gate.py'} --finish"
    lines = [f"create-skill-autoresearch: the build is not finished. `{finish}` says:"]
    lines += [f"- {ws}: BUILD UNFINISHED: {reason}" for ws, reason in found]
    lines.append("Carry Phase 5 through 5.4 now (SKILL.md 5.1): do what it names, then re-run that command "
                 "(--workspace <build>) until it exits 0. If this turn is waiting on the user's answer to a "
                 "question, end it again with that question.")
    return "\n".join(lines)


def _log(found: list[tuple[Path, str]], host: str) -> None:
    for ws, reason in found:
        try:
            path = ws / LOG
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     "host": host, "reason": reason}) + "\n")
        except OSError:
            pass


def decide(event: dict) -> dict:
    if event.get("hook_event_name") in SUBAGENT_EVENTS or event.get("agent_id"):
        return {}
    cursor = event.get("hook_event_name") == CURSOR_EVENT
    if cursor:
        if event.get("status") != "completed" or event.get("loop_count", 0) != 0:
            return {}
        roots = [Path(r) for r in event.get("workspace_roots") or [] if isinstance(r, str)]
    else:
        if event.get("stop_hook_active") is not False:
            return {}
        roots = [Path(event["cwd"])] if isinstance(event.get("cwd"), str) else []
    found = unfinished(roots)
    if not found:
        return {}
    _log(found, "cursor" if cursor else "stop")
    text = message(found)
    return {"followup_message": text} if cursor else {"decision": "block", "reason": text}


def _roots(event: dict) -> list[Path]:
    roots = [r for r in event.get("workspace_roots") or [] if isinstance(r, str)] or [event.get("cwd")]
    return [Path(r) for r in roots if isinstance(r, str)]


def background(event: dict) -> bool:
    """A subagent dispatch that will not run in the foreground: on Claude Code's Agent tool unless
    run_in_background is false (it defaults to the background), else only when it is true."""
    tool, args = event.get("tool_name"), event.get("tool_input")
    if not isinstance(args, dict):
        return False
    if event.get("hook_event_name") == CURSOR_PRE_TOOL:  # Cursor may wrap the call: {toolName, arguments, ...}
        inner = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        is_task = tool == "Task" or args.get("toolName") == "Task"
        return is_task and True in (args.get("run_in_background"), inner.get("run_in_background"))
    if tool == "Agent":
        return args.get("run_in_background") is not False
    return tool == "Task" and args.get("run_in_background") is True


def subagent_main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("the event is not a JSON object")
        cursor = event.get("hook_event_name") == CURSOR_PRE_TOOL
        deny = background(event) and any(builds(root, started=True) for root in _roots(event))
    except Exception as exc:  # noqa: BLE001  (the guard must never stop every subagent on its own failure)
        print(f"subagent guard: {type(exc).__name__}: {exc}; subagent allowed", file=sys.stderr)
        print(json.dumps(ALLOW))
        return 0
    if cursor:
        print(json.dumps({"permission": "deny", "user_message": FOREGROUND, "agent_message": FOREGROUND}
                         if deny else ALLOW))
        return 0
    if deny:
        print(FOREGROUND, file=sys.stderr)
        return 2
    return 0


def main() -> int:
    if "--subagent" in sys.argv[1:]:
        return subagent_main()
    try:
        event = json.load(sys.stdin)
        reply = decide(event) if isinstance(event, dict) else {}
    except Exception as exc:  # noqa: BLE001  (the guard must never trap a turn on its own failure)
        print(f"finish guard: {type(exc).__name__}: {exc}; turn allowed to end", file=sys.stderr)
        reply = {}
    print(json.dumps(reply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
