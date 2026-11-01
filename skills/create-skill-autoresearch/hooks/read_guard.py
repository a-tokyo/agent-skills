#!/usr/bin/env python3
"""File-read hook: keep the factory's own files out of a build session's context twice, or at all.

  - Script and hook source (this skill's scripts/*.py, hooks/*) is denied: each script's `--help` says how to run
    it, and the hooks act on their own.
  - A second full read of the same factory reference (references/**) in one context is denied: the first copy is
    still there. A context compaction ends that context, so after one the file reads again. Only a host that gives
    the session transcript can show the earlier read; with none, every reference read is allowed.

It reads the Read tool (Claude Code PreToolUse, Cursor beforeReadFile, the OpenCode plugin's `read`) here, and shell
reads (`cat`, `sed`, `head`, `tail`, `less`, ... of those paths) through hooks/kill_guard.py, which calls
`shell_block`. Like the kill guard it acts only in a factory build session (kill_guard.guarded), and it only ever
denies paths inside this skill's directory: the skill being built, the task materials and work/ always read. Input
it cannot read, or an error of its own, allows the read.

  Claude Code  PreToolUse Read {"tool_input": {"file_path", "offset", "limit"}, "cwd", "transcript_path",
               "agent_id"}: deny = exit 2, reason on stderr. A subagent's reads are matched against its own
               transcript (<session>/subagents/agent-<agent_id>.jsonl), never the main session's.
  Cursor       beforeReadFile {"file_path", "workspace_roots"}: always JSON {"permission": "allow"} or
               {"permission": "deny", "user_message", "agent_message"}; no transcript, so no duplicate check.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.dont_write_bytecode = True
import kill_guard  # noqa: E402  (beside this file; it holds the build test and the shell splitter)

FACTORY = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CURSOR_EVENT = "beforeReadFile"
ALLOW = {"permission": "allow"}
READERS = {"cat", "less", "more", "bat", "nl", "head", "tail", "sed", "awk"}
FULL_READERS = {"cat", "less", "more", "bat", "nl"}
WHOLE_SED = re.compile(r"(?:1,)?\$?p|p")  # sed -n p / sed -n 1,$p: the whole file
REDIRECT_ALONE = re.compile(r"\d*(?:>>?|<<-?|<)&?")
REDIRECT = re.compile(r"\d*(?:>|<)")
ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_]\w*)=(\S*)$")
VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
COMPACTION = ('"compact_boundary"', '"isCompactSummary":true', '"isCompactSummary": true')
SOURCE = ("Blocked by create-skill-autoresearch's read guard: {rel} is the factory's own source, not part of the "
          "build. {how}")
DUPLICATE = ("Blocked by create-skill-autoresearch's read guard: {rel} is already in your context (read in full "
             "earlier in this session); use that copy. After a context compaction it reads again.")


def factory_file(path: str, cwd: str | None) -> tuple[str, str] | None:
    """(kind, path relative to the skill) for a file inside this skill: "source" (scripts/*.py, hooks/*) or
    "reference" (references/**); None for anything else, the build's own files included."""
    if not isinstance(path, str) or not path or "$" in path:
        return None
    full = os.path.realpath(os.path.join(cwd or os.getcwd(), os.path.expanduser(path)))
    if not full.startswith(FACTORY + os.sep):
        return None
    rel = os.path.relpath(full, FACTORY).replace(os.sep, "/")
    if (rel.startswith("scripts/") and rel.endswith(".py")) or rel.startswith("hooks/"):
        return "source", rel
    if rel.startswith("references/"):
        return "reference", rel
    return None


def source_reason(rel: str) -> str:
    if rel.startswith("scripts/"):
        how = f"Run `python3 {FACTORY}/{rel} --help` for how to use it."
    else:
        how = "The hooks run on their own; references/hosts.md says what each one does."
    return SOURCE.format(rel=rel, how=how)


def shell_reads(command: str, cwd: str | None) -> list[tuple[str, bool, str | None]]:
    """[(path as written, whole file?, the directory it resolves against)] for each file a reader command
    (cat, sed, head, ...) in `command` reads, after the call's own `NAME=value` assignments and `cd`s."""
    out: list[tuple[str, bool, str | None]] = []
    names: dict[str, str] = {"HOME": os.path.expanduser("~")}
    here = cwd

    def expand(word: str) -> str:
        return VAR.sub(lambda m: names.get(m.group(1) or m.group(2), m.group(0)), word)

    for simple in kill_guard.simple_commands(kill_guard.normalise(command)):
        for segment in simple.split("|"):
            words = [expand(w) for w in kill_guard.WRAPPERS.sub("", segment.strip()).split()]
            while words and (m := ASSIGN.match(words[0])):
                names[m.group(1)] = m.group(2)
                words = words[1:]
            if words[:1] == ["export"]:
                words = words[1:]
            if not words:
                continue
            if words[0] == "cd" and len(words) > 1 and "$" not in words[1]:
                here = os.path.join(here or os.getcwd(), os.path.expanduser(words[1]))
                continue
            tool = os.path.basename(words[0])
            if tool not in READERS:
                continue
            args, skip, script = [], False, tool in ("sed", "awk")
            sed_script = None
            for word in words[1:]:
                if skip:
                    skip = False
                    continue
                if REDIRECT_ALONE.fullmatch(word):  # `> file`, `2> file`, `<< EOF`: the next word is its target
                    skip = True
                    continue
                if REDIRECT.match(word):  # `>file`, `2>&1`, `<<EOF`
                    continue
                if word in ("-e", "-f") and script:
                    skip = True
                    continue
                if word.startswith("-"):
                    continue
                if script and sed_script is None:
                    sed_script = word
                    continue
                args.append(word)
            whole = tool in FULL_READERS or (tool == "sed" and bool(sed_script and WHOLE_SED.fullmatch(sed_script)))
            out.extend((a, whole, here) for a in args)
    return out


def transcript(event: dict) -> str | None:
    """The transcript of the context making this call: a subagent's own file when agent_id names one."""
    path = event.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    agent = event.get("agent_id")
    if isinstance(agent, str) and agent:
        base = path[:-len(".jsonl")] if path.endswith(".jsonl") else path
        path = os.path.join(base, "subagents", f"agent-{agent}.jsonl")
    return path if os.path.isfile(path) else None


def read_before(path: str, rel: str, cwd: str | None, current: object = None) -> bool:
    """Whether the transcript at `path` shows a whole read of factory file `rel` since its last compaction that
    did not fail (a denied or failed read put nothing in context), other than the call being checked (`current`,
    the event's tool_use_id, should the host have written it already)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    start = max((i for i, line in enumerate(lines) if any(m in line for m in COMPACTION)), default=-1) + 1
    name = rel.rsplit("/", 1)[-1]
    reads, failed = set(), set()
    for line in lines[start:]:
        if name not in line and '"is_error"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (entry.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and block.get("is_error"):
                failed.add(block.get("tool_use_id"))
            if block.get("type") != "tool_use" or not isinstance(block.get("input"), dict):
                continue
            args, here = block["input"], entry.get("cwd") if isinstance(entry.get("cwd"), str) else cwd
            if block.get("name") == "Read":
                found = [(args.get("file_path"), args.get("offset") is None and args.get("limit") is None, here)]
            elif block.get("name") == "Bash" and isinstance(args.get("command"), str):
                found = shell_reads(args["command"], here)
            else:
                continue
            if any(whole and (factory_file(p, at) or ("", ""))[1] == rel for p, whole, at in found):
                reads.add(block.get("id"))
    return bool(reads - failed - {current})


def check(reads: list[tuple[str, bool, str | None]], cwd: str | None, event: dict) -> str | None:
    """The deny reason for these reads in this session, or None."""
    if not reads or not kill_guard.guarded(cwd):
        return None
    log = None
    for path, whole, at in reads:
        found = factory_file(path, at)
        if not found:
            continue
        kind, rel = found
        if kind == "source":
            return source_reason(rel)
        if whole:
            log = log or transcript(event)
            if log and read_before(log, rel, cwd, event.get("tool_use_id")):
                return DUPLICATE.format(rel=rel)
    return None


def shell_block(command: str, cwd: str | None, event: dict | None) -> str | None:
    """kill_guard.py's entry: the deny reason for a shell call that reads factory files, or None."""
    try:
        return check(shell_reads(command, cwd), cwd, event or {})
    except Exception as exc:  # noqa: BLE001  (never stop a shell call on the guard's own failure)
        print(f"read guard: {type(exc).__name__}: {exc}; read allowed", file=sys.stderr)
        return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        event = None
    if not isinstance(event, dict):
        print(json.dumps(ALLOW))
        return 0
    cursor = event.get("hook_event_name") == CURSOR_EVENT
    try:
        if cursor:
            roots = [r for r in event.get("workspace_roots") or [] if isinstance(r, str)]
            cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else (roots[0] if roots else None)
            reason = check([(event.get("file_path"), True, cwd)], cwd, {})
        else:
            args = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
            cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else None
            whole = args.get("offset") is None and args.get("limit") is None
            reason = check([(args.get("file_path"), whole, cwd)], cwd, event)
    except Exception as exc:  # noqa: BLE001  (never stop a read on the guard's own failure)
        print(f"read guard: {type(exc).__name__}: {exc}; read allowed", file=sys.stderr)
        reason = None
    if cursor:
        print(json.dumps({"permission": "deny", "user_message": reason, "agent_message": reason} if reason else ALLOW))
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
