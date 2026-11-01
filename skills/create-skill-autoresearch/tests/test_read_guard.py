"""Offline tests for hooks/read_guard.py (the read guard), its launcher hooks/read-guard, its shell half in
hooks/kill_guard.py, the OpenCode plugin's `read` and the hook configs.

Each case feeds a hook the JSON a host sends on stdin: Claude Code exits 2 to deny (reason on stderr) and 0 to
allow; Cursor always gets JSON. Transcripts are synthesized in Claude Code's jsonl shape (one line per content
block; `compact_boundary` and `isCompactSummary` mark a compaction).

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
READ_HOOK = SKILL / "hooks" / "read_guard.py"
KILL_HOOK = SKILL / "hooks" / "kill_guard.py"
REFERENCE = SKILL / "references" / "phase-4.md"
SCRIPT = SKILL / "scripts" / "check_gate.py"
SH = "/bin/sh"
IDS = itertools.count()


def env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "CSA_EXECUTOR"} | {"PYTHONDONTWRITEBYTECODE": "1"}


def run(hook: Path, event: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(hook)], input=json.dumps(event), capture_output=True,
                          text=True, timeout=30, env=env())


class Transcript:
    """A Claude Code session file: tool calls (with or without a failing result) and compactions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "build it"}}) + "\n")

    def _add(self, *entries: dict) -> None:
        with open(self.path, "a") as fh:
            fh.write("".join(json.dumps(e) + "\n" for e in entries))

    def call(self, name: str, args: dict, error: bool = False) -> None:
        tid = f"toolu_{next(IDS)}"
        self._add({"type": "assistant", "cwd": "/", "message": {"role": "assistant", "content": [
                       {"type": "tool_use", "id": tid, "name": name, "input": args}]}},
                  {"type": "user", "message": {"role": "user", "content": [
                       {"type": "tool_result", "tool_use_id": tid, "content": "x", "is_error": error}]}})

    def read(self, path: Path, **extra: object) -> None:
        self.call("Read", {"file_path": str(path), **extra})

    def compact(self, marker: str) -> None:
        if marker == "boundary":
            self._add({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"})
        else:
            self._add({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "summary"}})


class ReadGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.build = self.root / "build"
        (self.build / "work").mkdir(parents=True)
        (self.build / "work" / "manifest.yaml").write_text("pairs: []\n")
        own = self.build / "output" / "demo" / "scripts"
        own.mkdir(parents=True)
        (own / "parse_request.py").write_text("print('hi')\n")
        (self.build / "output" / "demo" / "references").mkdir()
        (self.build / "output" / "demo" / "references" / "phase-4.md").write_text("the built skill's own\n")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.log = Transcript(self.root / "session.jsonl")

    def read(self, path: Path | str, cwd: Path | None = None, agent: str | None = None, transcript: bool = True,
             **extra: object) -> subprocess.CompletedProcess:
        event = {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": str(cwd or self.build),
                 "tool_input": {"file_path": str(path), **extra}}
        if transcript:
            event["transcript_path"] = str(self.log.path)
        if agent:
            event["agent_id"] = agent
        return run(READ_HOOK, event)

    def bash(self, command: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return run(KILL_HOOK, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd or self.build),
                               "transcript_path": str(self.log.path), "tool_input": {"command": command}})

    def assertDenied(self, out: subprocess.CompletedProcess, says: str) -> None:
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("read guard", out.stderr)
        self.assertIn(says, out.stderr)

    def assertAllowed(self, out: subprocess.CompletedProcess, why: str = "") -> None:
        self.assertEqual(out.returncode, 0, f"{why} {out.stderr}")

    # Script and hook source

    def test_script_and_hook_source_are_denied_with_a_help_pointer(self) -> None:
        out = self.read(SCRIPT)
        self.assertDenied(out, f"python3 {SCRIPT} --help")
        self.assertDenied(self.read(SCRIPT, offset=60, limit=40), "--help")
        self.assertDenied(self.read(SKILL / "hooks" / "kill_guard.py"), "hooks run on their own")
        self.assertDenied(self.read(SKILL / "hooks" / "hooks.json"), "factory's own source")

    def test_shell_reads_of_source_are_denied_the_ways_d18_wrote_them(self) -> None:
        for command in (f"cat {SKILL}/scripts/check_gate.py",
                        f"F={SKILL}; python3 $F/scripts/check_gate.py --panel; cat $F/scripts/panel.py",
                        f"FAC={SKILL} && sed -n 60,125p ${{FAC}}/scripts/panel.py",
                        f"cd {SKILL} && sed -n 68,120p scripts/panel.py",
                        f"head -50 {SKILL}/scripts/gate.py | grep def",
                        f"cat '{SKILL}/hooks/kill_guard.py' 2>/dev/null"):
            with self.subTest(command=command):
                self.assertDenied(self.bash(command), "factory's own source")

    def test_running_a_script_or_its_help_is_allowed(self) -> None:
        for command in (f"python3 {SCRIPT} --help", f"S={SKILL}/scripts; python3 $S/check_gate.py --finish",
                        f"python3 {SKILL}/scripts/rescore.py", f"grep -n loop {SKILL}/references/phase-4.md",
                        f"cat > {self.build}/work/notes.md <<'EOF'\nscripts/panel.py\nEOF", "cat $UNKNOWN/x.py"):
            with self.subTest(command=command):
                self.assertAllowed(self.bash(command))

    # The skill under construction, the task materials and work/

    def test_the_build_s_own_files_always_read(self) -> None:
        own = self.build / "output" / "demo"
        self.assertAllowed(self.read(own / "scripts" / "parse_request.py"))
        self.log.read(own / "references" / "phase-4.md")
        self.assertAllowed(self.read(own / "references" / "phase-4.md"))
        for command in ("cat output/demo/scripts/parse_request.py", "sed -n 1,20p output/demo/references/phase-4.md",
                        "cat work/manifest.yaml", f"cat {self.build}/input/request.md"):
            with self.subTest(command=command):
                self.assertAllowed(self.bash(command))

    def test_outside_a_build_nothing_is_denied(self) -> None:
        self.log.read(REFERENCE)
        self.assertAllowed(self.read(SCRIPT, cwd=self.outside))
        self.assertAllowed(self.read(REFERENCE, cwd=self.outside))
        self.assertAllowed(self.bash(f"cat {SCRIPT}", cwd=self.outside))

    # A second whole read of a reference

    def test_a_first_reference_read_is_allowed(self) -> None:
        self.log.read(SKILL / "references" / "phase-3.md")
        self.assertAllowed(self.read(REFERENCE))
        self.assertAllowed(self.bash(f"cat {REFERENCE}"))

    def test_a_second_whole_read_is_denied_whichever_tool_made_the_first(self) -> None:
        self.log.read(REFERENCE)
        self.assertDenied(self.read(REFERENCE), "references/phase-4.md is already in your context")
        self.assertDenied(self.bash(f"cat {REFERENCE}"), "already in your context")
        self.assertDenied(self.bash(f"G={SKILL}; cat $G/references/phase-4.md | head -150"), "already in your context")
        log = Transcript(self.root / "other.jsonl")
        self.log = log
        log.call("Bash", {"command": f"cd {SKILL} && cat references/phase-4.md"})
        self.assertDenied(self.read(REFERENCE), "already in your context")

    def test_ranged_and_failed_reads_do_not_count(self) -> None:
        self.log.read(REFERENCE, offset=1, limit=40)
        self.log.call("Bash", {"command": f"sed -n 1,40p {REFERENCE}"})
        self.log.call("Read", {"file_path": str(REFERENCE)}, error=True)  # denied or failed: nothing in context
        self.assertAllowed(self.read(REFERENCE))
        self.log.read(REFERENCE)
        self.assertAllowed(self.read(REFERENCE, offset=10, limit=20))
        self.assertAllowed(self.bash(f"sed -n 10,30p {REFERENCE}"))

    def test_a_compaction_allows_it_again(self) -> None:
        for marker in ("boundary", "summary"):
            with self.subTest(marker=marker):
                self.log = Transcript(self.root / f"{marker}.jsonl")
                self.log.read(REFERENCE)
                self.assertDenied(self.read(REFERENCE), "already in your context")
                self.log.compact(marker)
                self.assertAllowed(self.read(REFERENCE))
                self.log.read(REFERENCE)
                self.assertDenied(self.read(REFERENCE), "already in your context")

    def test_the_call_being_checked_is_not_its_own_earlier_read(self) -> None:
        self.log.call("Read", {"file_path": str(REFERENCE)})
        current = json.loads(self.log.path.read_text().splitlines()[1])["message"]["content"][0]["id"]
        event = {"tool_name": "Read", "cwd": str(self.build), "transcript_path": str(self.log.path),
                 "tool_use_id": current, "tool_input": {"file_path": str(REFERENCE)}}
        self.assertAllowed(run(READ_HOOK, event))

    def test_without_a_transcript_reference_reads_are_allowed(self) -> None:
        self.log.read(REFERENCE)
        self.assertAllowed(self.read(REFERENCE, transcript=False))
        self.assertDenied(self.read(SCRIPT, transcript=False), "--help")

    def test_a_subagent_is_matched_against_its_own_transcript(self) -> None:
        self.log.read(REFERENCE)
        self.assertAllowed(self.read(REFERENCE, agent="a1"), "no subagent transcript yet: its context is fresh")
        sub = self.root / "session" / "subagents"
        sub.mkdir(parents=True)
        own = Transcript(sub / "agent-a1.jsonl")
        self.assertAllowed(self.read(REFERENCE, agent="a1"))
        own.read(REFERENCE)
        self.assertDenied(self.read(REFERENCE, agent="a1"), "already in your context")

    def test_unreadable_input_and_broken_transcripts_allow(self) -> None:
        out = subprocess.run([sys.executable, "-B", str(READ_HOOK)], input="not json", capture_output=True,
                             text=True, timeout=30, env=env())
        self.assertEqual((out.returncode, json.loads(out.stdout)), (0, {"permission": "allow"}))
        self.log.path.write_text("{broken\n" + json.dumps({"type": "assistant"}) + "\n")
        self.assertAllowed(self.read(REFERENCE))

    # Cursor

    def test_cursor_before_read_file_answers_json(self) -> None:
        def cursor(path: Path) -> dict:
            out = run(READ_HOOK, {"hook_event_name": "beforeReadFile", "file_path": str(path), "content": "",
                                  "workspace_roots": [str(self.build)], "conversation_id": "c"})
            self.assertEqual(out.returncode, 0, out.stderr)
            return json.loads(out.stdout)
        deny = cursor(SCRIPT)
        self.assertEqual(deny["permission"], "deny")
        self.assertIn("--help", deny["agent_message"])
        self.log.read(REFERENCE)
        self.assertEqual(cursor(REFERENCE), {"permission": "allow"}, "no transcript: no duplicate check")
        self.assertEqual(cursor(self.build / "output" / "demo" / "scripts" / "parse_request.py"), {"permission": "allow"})


def run_configured(command: str, variable: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([SH, "-c", command.replace("${" + variable + "}", str(SKILL))], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env())


class ReadGuardConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.build = Path(tmp.name)
        (self.build / "work").mkdir()
        (self.build / "work" / "manifest.yaml").write_text("pairs: []\n")

    def test_claude_read_hook_runs_the_launcher_and_fails_open(self) -> None:
        config = json.loads((SKILL / "hooks" / "hooks.json").read_text())
        [entry] = [e for e in config["hooks"]["PreToolUse"] if e["matcher"] == "Read"]
        [hook] = entry["hooks"]
        self.assertEqual((hook["command"], hook["onFailure"]), ('sh "${CLAUDE_PLUGIN_ROOT}/hooks/read-guard"', "continue"))
        out = run_configured(hook["command"], "CLAUDE_PLUGIN_ROOT", {"tool_name": "Read", "cwd": str(self.build),
                                                                     "tool_input": {"file_path": str(SCRIPT)}})
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("--help", out.stderr)
        self.assertTrue(os.access(SKILL / "hooks" / "read-guard", os.X_OK))

    def test_cursor_before_read_file_hook_runs_the_launcher(self) -> None:
        manifest = json.loads((SKILL / ".cursor-plugin" / "plugin.json").read_text())
        [hook] = json.loads((SKILL / manifest["hooks"]).read_text())["hooks"]["beforeReadFile"]
        out = run_configured(hook["command"], "CURSOR_PLUGIN_ROOT", {"hook_event_name": "beforeReadFile",
                                                                     "file_path": str(SCRIPT),
                                                                     "workspace_roots": [str(self.build)]})
        self.assertEqual((out.returncode, json.loads(out.stdout)["permission"]), (0, "deny"))
        self.assertIn("read guard", manifest["description"])

    def test_launcher_without_python_allows_with_json_for_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for tool in ("cat",):
                os.symlink(shutil.which(tool), Path(tmp) / tool)
            out = subprocess.run([SH, str(SKILL / "hooks" / "read-guard")], input=json.dumps(
                {"hook_event_name": "beforeReadFile", "file_path": str(SCRIPT)}), capture_output=True, text=True,
                timeout=30, env={"PATH": tmp})
            self.assertEqual((out.returncode, json.loads(out.stdout)), (0, {"permission": "allow"}))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class OpenCodeReadTest(unittest.TestCase):
    """hooks/opencode-kill-guard.js: tool.execute.before on `read` runs the read guard."""

    PLUGIN = (SKILL / "hooks" / "opencode-kill-guard.js").as_uri()

    def call(self, file_path: Path, directory: Path) -> str:
        script = f"""
            import {{ KillGuard }} from {json.dumps(self.PLUGIN)};
            const hooks = await KillGuard({{ directory: {json.dumps(str(directory))} }});
            try {{
              await hooks["tool.execute.before"]({{ tool: "read" }}, {{ args: {{ filePath: {json.dumps(str(file_path))} }} }});
              console.log("allowed");
            }} catch (error) {{ console.log("blocked: " + error.message); }}
        """
        out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", textwrap.dedent(script)],
                             capture_output=True, text=True, timeout=60, env=env())
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_script_source_is_blocked_and_references_and_the_build_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / "work").mkdir()
            (build / "work" / "manifest.yaml").write_text("pairs: []\n")
            self.assertTrue(self.call(SCRIPT, build).startswith("blocked: Blocked by create-skill-autoresearch's read"))
            self.assertEqual(self.call(REFERENCE, build), "allowed", "no transcript: no duplicate check")
            self.assertEqual(self.call(build / "work" / "manifest.yaml", build), "allowed")


if __name__ == "__main__":
    unittest.main()
