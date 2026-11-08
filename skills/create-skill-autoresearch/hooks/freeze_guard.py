#!/usr/bin/env python3
"""Write hook: once the panel reviewed a build's skill, output/<skill>/ changes only inside a fix cycle.

After a panel round's first receipt (panel.started), a write to a build's output/<skill>/ is denied unless a fix
cycle is open (fix_cycle.open_now): a change the panel did not review, outside a cycle, ends the build UNSHIPPED,
and a fix cycle is how a cited finding gets fixed and re-checked (references/fix-cycle.md). The deny message names
the exact `check_gate.py --fix-cycle --finding <id>` command for the findings the written file is cited by (else
every open finding), or says why no cycle can open. On the panel-verdict path the skill is not frozen while the
Phase 4 loop is open (an ITERATE or BLOCK sends the build back to it). Only output/ is guarded: work/ and input/
always write. It acts only on a build (a directory holding work/manifest.yaml or work/evaluation/ beside output/),
never in an executor session (CSA_EXECUTOR set); input it cannot read, or an error of its own, allows the write.

  Claude Code  PreToolUse Edit|Write|MultiEdit|NotebookEdit {"tool_input": {"file_path" | "notebook_path"},
               "cwd"}: deny = exit 2, reason on stderr.
  Cursor       preToolUse on its file-editing tools {"tool_input": {"file_path" | "path" | "target_file"}}:
               always JSON {"permission": "allow"} or {"permission": "deny", "user_message", "agent_message"}.
  OpenCode     hooks/opencode-kill-guard.js sends `edit`, `write` and `patch` here as a Claude Code event.

Shell writes (a redirect, `tee`, `cp`, `mv`, `rm`, `touch`, `sed -i`, ...) reach it through hooks/kill_guard.py,
which calls `shell_block`. Like the kill guard it is a tripwire for honest mistakes, not a sandbox: a heredoc fed to
an interpreter or a `python -c` write is not parsed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, HERE)
import kill_guard  # noqa: E402  (beside this file: the shell splitter)

ALLOW = {"permission": "allow"}
PATH_KEYS = ("file_path", "notebook_path", "path", "target_file", "filePath")
WRITERS = {"cp", "mv", "rm", "touch", "mkdir", "rmdir", "tee", "install", "ln", "rsync", "truncate", "patch",
           "unlink", "dd"}
IN_PLACE = {"sed", "gsed", "perl"}
COPIERS = {"cp", "install", "ln", "rsync"}
REDIRECT = re.compile(r"^\d*>>?\|?(.*)$")  # `>file`, `2>>file`, `>|file`, or a lone `>` (target is the next word)
ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_]\w*)=(\S*)$")
VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
HEAD = ("Blocked by create-skill-autoresearch's freeze guard: the panel reviewed output/{skill}/ ({ws}), so a change "
        "outside a fix cycle ends the build UNSHIPPED.")


def build_of(path: str, cwd: str | None) -> tuple[str, str] | None:
    """(workspace, skill) when `path` lies under a build's output/<skill>/ (or names output/ itself), else None."""
    if not isinstance(path, str) or not path or "$" in path:
        return None
    full = os.path.normpath(os.path.join(cwd or os.getcwd(), os.path.expanduser(path)))
    parts = full.split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] != "output":
            continue
        ws = os.sep.join(parts[:i]) or os.sep
        if os.path.isfile(os.path.join(ws, "work", "manifest.yaml")) or os.path.isdir(os.path.join(ws, "work",
                                                                                                   "evaluation")):
            skill = parts[i + 1] if i + 1 < len(parts) else ""
            return (ws, skill) if skill else None
    return None


def shell_targets(command: str, cwd: str | None) -> list[tuple[str, str | None]]:
    """[(path as written, the directory it resolves against)] for each file a shell call writes: redirect targets,
    the operands of cp, mv, rm, touch, tee, ... (dd's of=), and the files of an in-place sed or perl."""
    out: list[tuple[str, str | None]] = []
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
            if not words:
                continue
            if words[0] == "cd" and len(words) > 1 and "$" not in words[1]:
                here = os.path.join(here or os.getcwd(), os.path.expanduser(words[1]))
                continue
            rest, operands = words[1:], []
            i = 0
            while i < len(rest):
                word = rest[i]
                if (m := REDIRECT.match(word)) and not word.startswith(">&") and "&" not in word[:3]:
                    target = m.group(1) or (rest[i + 1] if i + 1 < len(rest) else "")
                    i += 1 if m.group(1) else 2
                    if target and target != "/dev/null" and not target.startswith("&"):
                        out.append((target, here))
                    continue
                if re.match(r"^\d*<", word):  # input redirect: skip its target
                    i += 1 if len(word.lstrip("0123456789")) > 1 else 2
                    continue
                operands.append(word)
                i += 1
            tool = os.path.basename(words[0])
            if tool in WRITERS:
                files = [w for w in operands if not w.startswith("-")]
                if tool == "dd":
                    out.extend((w[3:], here) for w in operands if w.startswith("of="))
                elif tool in COPIERS:  # only the destination is written (mv also removes its sources)
                    out.extend((w, here) for w in files[-1:])
                else:
                    out.extend((w, here) for w in operands if not w.startswith("-"))
            elif tool in IN_PLACE and any(w.startswith("-i") or w == "--in-place" for w in operands):
                files = [w for w in operands if not w.startswith("-")]
                out.extend((w, here) for w in files[1:])  # the first is the script
    return out


def _scripts():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import check_gate  # noqa: PLC0415  (the skill's own scripts)
    import fix_cycle  # noqa: PLC0415
    import panel  # noqa: PLC0415
    return check_gate, fix_cycle, panel


def frozen(ws: str, skill: str) -> bool:
    """The panel started a round on this skill, no fix cycle is open, and (panel-verdict path) the Phase 4 loop is
    not open."""
    from pathlib import Path  # noqa: PLC0415
    check_gate, fix_cycle, panel = _scripts()
    root = Path(ws).resolve()
    if not any(name == skill for _, name in panel.started(root)) or fix_cycle.open_now(root):
        return False
    return panel.checkable(root) or check_gate.loop_done(root, skill)[0]


def message(ws: str, skill: str, rel: str | None) -> str:
    """The deny reason: the command that opens a fix cycle on the findings `rel` (the written file under
    output/<skill>/) is cited by, else on any open finding; or why none can open."""
    from pathlib import Path  # noqa: PLC0415
    check_gate, fix_cycle, _ = _scripts()
    root = Path(ws).resolve()
    head = HEAD.format(skill=skill, ws=ws)
    try:
        offered = fix_cycle.offer(root, skill)
    except Exception:  # noqa: BLE001  (an offer it cannot compute: say so, still deny)
        offered = None
    nxt = check_gate._self(root, "--next")
    if offered is None:
        return (f"{head} No fix cycle can open now (none left, no cited finding, or the gate or metric already "
                f"allows only UNSHIPPED): edit nothing in output/{skill}/. Next step: {nxt}")

    def cites(f: dict) -> bool:
        if not rel:
            return False
        evidence = f.get("evidence") or ""
        if rel in evidence:
            return True
        try:
            with open(os.path.join(ws, "output", skill, rel), encoding="utf-8", errors="replace") as fh:
                text = " ".join(fh.read().split())
        except OSError:
            return False
        quotes = [" ".join((a or b).split()) for a, b in check_gate.panel.QUOTED.findall(evidence)]
        return any(q and q in text for q in quotes)

    mine = [f for f in offered.findings if cites(f)]
    shown = mine or offered.findings
    command = check_gate._self(root, "--fix-cycle", *[f"--finding {shlex.quote(f['id'])}" for f in shown])
    listed = "\n".join(f"  {f['id']}: {f.get('evidence', '')}" for f in shown[:8])
    which = f"the finding(s) citing {rel}" if mine else "a finding (drop the --finding ids you will not fix)"
    return (f"{head} To fix {which}, open fix cycle {offered.n} first, then edit only the lines each finding "
            f"cites; --next names every later step:\n  {command}\nFindings:\n{listed}")


def check(paths: list[tuple[str, str | None]], cwd: str | None) -> str | None:
    """The deny reason for writing these paths, or None."""
    if os.environ.get("CSA_EXECUTOR"):
        return None
    for path, at in paths:
        if any(part == "__pycache__" or part == ".DS_Store" or part.endswith(".pyc")
               for part in str(path).replace("\\", "/").split("/")):
            continue  # not part of the skill: gate_common's hashes skip them
        found = build_of(path, at or cwd)
        if found is None:
            continue
        ws, skill = found
        if frozen(ws, skill):
            full = os.path.normpath(os.path.join(at or cwd or os.getcwd(), os.path.expanduser(path)))
            rel = os.path.relpath(full, os.path.join(ws, "output", skill)).replace(os.sep, "/")
            return message(ws, skill, None if rel.startswith("..") or rel == "." else rel)
    return None


def shell_block(command: str, cwd: str | None) -> str | None:
    """kill_guard.py's entry: the deny reason for a shell call that writes a frozen output/<skill>/, or None."""
    if "output" not in command:
        return None
    try:
        return check(shell_targets(command, cwd), cwd)
    except Exception as exc:  # noqa: BLE001  (never stop a shell call on the guard's own failure)
        print(f"freeze guard: {type(exc).__name__}: {exc}; command allowed", file=sys.stderr)
        return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        event = None
    if not isinstance(event, dict):
        print(json.dumps(ALLOW))
        return 0
    cursor = event.get("hook_event_name") == "preToolUse"
    reason = None
    try:
        args = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        roots = [r for r in event.get("workspace_roots") or [] if isinstance(r, str)]
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else (roots[0] if roots else None)
        paths = [(args[k], cwd) for k in PATH_KEYS if isinstance(args.get(k), str)]
        reason = check(paths, cwd)
    except Exception as exc:  # noqa: BLE001  (never stop a write on the guard's own failure)
        print(f"freeze guard: {type(exc).__name__}: {exc}; write allowed", file=sys.stderr)
    if cursor:
        print(json.dumps({"permission": "deny", "user_message": reason, "agent_message": reason} if reason else ALLOW))
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
