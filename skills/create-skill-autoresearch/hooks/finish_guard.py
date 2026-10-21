#!/usr/bin/env python3
"""End-of-turn hook: a factory build does not end before `check_gate.py --finish` exits 0 (SKILL.md 5.1).

It acts only inside a factory build workspace — a directory holding `work/evaluation/` and
`output/<skill>/SKILL.md`: the session's directory itself or a `builds/<name>/` under it (SKILL.md 1.6).
For each such build it runs scripts/check_gate.py's finish check; if one is unfinished, it sends the agent
back once with the validator's reason, and logs that to work/experiments/finish-guard.jsonl in the build.
It never sends the agent back twice in a row, and lets the turn end on input it cannot read or on any
error of its own. Contracts (references/hosts.md):

  Claude Code, Codex  Stop, stdin {"hook_event_name": "Stop", "cwd", "stop_hook_active", ...}. Back once:
                      {"decision": "block", "reason": ...}; else {}. stop_hook_active true (already sent
                      back by a Stop hook) always ends the turn.
  Cursor              stop, stdin {"hook_event_name": "stop", "status", "loop_count", "workspace_roots"}.
                      Back once: {"followup_message": ...} when status is "completed" and loop_count is 0;
                      else {}. The plugin config also sets loop_limit 1.
  OpenCode            hooks/opencode-finish-guard.js sends the Claude Code shape on session.idle of a
                      top-level session (a subagent's child session has a parentID and is skipped).

Only the main session's end of turn counts: phase workers, researchers and panel members (SKILL.md
"Orchestration") end on Claude Code's SubagentStop and Cursor's subagentStop, which the hook configs never
register; if one is wired here anyway, or a stop payload carries Claude Code's `agent_id` (set only inside a
subagent), the turn ends untouched.

hooks/finish-guard runs it with whichever Python 3 the machine has.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CURSOR_EVENT = "stop"
SUBAGENT_EVENTS = {"SubagentStop", "subagentStop"}
LOG = Path("work") / "experiments" / "finish-guard.jsonl"


def builds(root: Path) -> list[Path]:
    """The factory build workspaces at `root` or directly under root/builds/."""
    candidates = [root, *sorted(p for p in (root / "builds").glob("*") if p.is_dir())]
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


def main() -> int:
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
