"""Offline tests for hooks/finish_guard.py (the end-of-turn finish guard, and with --subagent the subagent
guard), its launcher hooks/finish-guard, the OpenCode plugin and the hook configs.

Each case feeds the hook the JSON a host sends at the end of a turn and checks its answer: Claude Code and
Codex get {"decision": "block", "reason"} once, Cursor {"followup_message"} once, and {} otherwise.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

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
HOOK = SKILL / "hooks" / "finish_guard.py"
LAUNCHER = SKILL / "hooks" / "finish-guard"
SH = "/bin/sh"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def stop(cwd: Path, active: bool = False) -> dict:
    return {"session_id": "s", "hook_event_name": "Stop", "cwd": str(cwd), "stop_hook_active": active,
            "last_assistant_message": "Phase 5.1 premortem written; next I will run the panel."}


def cursor_stop(root: Path, loop_count: int = 0, status: str = "completed") -> dict:
    return {"hook_event_name": "stop", "status": status, "loop_count": loop_count, "workspace_roots": [str(root)]}


class FinishGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.build(self.root / "ws")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_native_windows_lets_the_call_through_with_one_message(self) -> None:
        code = f"import os, pathlib, runpy, shutil, subprocess, tempfile; os.name = 'nt'; runpy.run_path({str(HOOK)!r}, run_name='__main__')"
        out = subprocess.run([sys.executable, "-B", "-c", code], input="{}", capture_output=True, text=True, timeout=30)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, '{}'))
        self.assertIn("native Windows is not supported; run the factory inside WSL", out.stderr)

    @staticmethod
    def build(ws: Path) -> Path:
        (ws / "work" / "evaluation").mkdir(parents=True)
        (ws / "output" / "demo").mkdir(parents=True)
        (ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n")
        return ws

    @staticmethod
    def finish(ws: Path) -> None:
        """An UNSHIPPED build whose gate step failed: check_gate.py --finish exits 0."""
        (ws / "work" / "evaluation" / "gate-attempts.jsonl").write_text(
            json.dumps({"at": "2026-10-19T00:00:00+00:00", "step": "gate", "error": "no run-case.sh"}) + "\n")
        (ws / "BENCHMARK.md").write_text("Verdict: UNSHIPPED\n")

    def hook(self, payload: object, cmd: list[str] | None = None) -> dict:
        data = payload if isinstance(payload, str) else json.dumps(payload)
        out = subprocess.run(cmd or [sys.executable, "-B", str(HOOK)], input=data, capture_output=True, text=True,
                             timeout=60, env=ENV)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_an_unfinished_build_is_sent_back_once_with_the_validators_reason(self) -> None:
        reply = self.hook(stop(self.ws))
        self.assertEqual(reply["decision"], "block")
        self.assertIn(f"{self.ws}: BUILD UNFINISHED: no BENCHMARK.md at the build root", reply["reason"])
        self.assertIn("check_gate.py --finish", reply["reason"])
        self.assertEqual(self.hook(stop(self.ws, active=True)), {}, "never twice in a row: stop_hook_active")
        [logged] = (self.ws / "work" / "experiments" / "finish-guard.jsonl").read_text().splitlines()
        self.assertIn("no BENCHMARK.md", json.loads(logged)["reason"])

    def test_a_finished_build_or_another_directory_ends_the_turn(self) -> None:
        self.finish(self.ws)
        self.assertEqual(self.hook(stop(self.ws)), {})
        plain = self.root / "plain"
        plain.mkdir()
        self.assertEqual(self.hook(stop(plain)), {}, "no work/evaluation/ and output/*/SKILL.md: not a build")
        (plain / "work" / "evaluation").mkdir(parents=True)
        self.assertEqual(self.hook(stop(plain)), {}, "no output/<skill>/SKILL.md yet")

    def test_a_build_under_builds_is_found_from_the_parent_project(self) -> None:
        nested = self.build(self.root / "project" / "builds" / "demo")
        reply = self.hook(stop(self.root / "project"))
        self.assertIn(f"{nested}: BUILD UNFINISHED", reply["reason"])
        self.finish(nested)
        self.assertEqual(self.hook(stop(self.root / "project")), {})

    def test_codex_shape_is_the_claude_shape(self) -> None:
        event = stop(self.ws) | {"turn_id": "t1", "model": "gpt-5.6", "permission_mode": "bypassPermissions"}
        self.assertEqual(self.hook(event)["decision"], "block")

    def test_cursor_gets_one_followup_on_a_completed_turn(self) -> None:
        reply = self.hook(cursor_stop(self.ws))
        self.assertIn("BUILD UNFINISHED", reply["followup_message"])
        self.assertEqual(self.hook(cursor_stop(self.ws, loop_count=1)), {}, "the follow-up already ran once")
        self.assertEqual(self.hook(cursor_stop(self.ws, status="aborted")), {}, "a user abort ends the turn")
        self.assertEqual(self.hook(cursor_stop(self.ws, status="error")), {})

    def test_a_subagent_stop_never_holds_the_turn(self) -> None:
        """Phase workers and panel members end on SubagentStop (Claude Code) / subagentStop (Cursor)."""
        claude = stop(self.ws) | {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": "general-purpose"}
        self.assertEqual(self.hook(claude), {}, "Claude Code SubagentStop on an unfinished build")
        self.assertEqual(self.hook(stop(self.ws) | {"agent_id": "a1"}), {}, "a stop inside a subagent")
        cursor = cursor_stop(self.ws) | {"hook_event_name": "subagentStop"}
        self.assertEqual(self.hook(cursor), {}, "Cursor subagentStop on an unfinished build")
        self.assertFalse((self.ws / "work" / "experiments" / "finish-guard.jsonl").exists(), "nothing logged")
        main = stop(self.ws) | {"agent_type": "factory"}
        self.assertEqual(self.hook(main)["decision"], "block", "a main session run with --agent is still guarded")

    def test_no_hook_config_registers_a_subagent_stop(self) -> None:
        claude = json.loads((SKILL / "hooks" / "hooks.json").read_text())["hooks"]
        self.assertNotIn("SubagentStop", claude)
        cursor = json.loads((SKILL / "hooks" / "cursor-hooks.json").read_text())["hooks"]
        self.assertNotIn("subagentStop", cursor)

    def test_unreadable_input_or_a_guard_error_ends_the_turn(self) -> None:
        self.assertEqual(self.hook("not json"), {})
        self.assertEqual(self.hook([1, 2]), {})
        self.assertEqual(self.hook({"hook_event_name": "Stop"}), {}, "no stop_hook_active: treated as already sent back")
        self.assertEqual(self.hook({"hook_event_name": "Stop", "stop_hook_active": False, "cwd": 7}), {})
        (self.ws / "BENCHMARK.md").write_text("Verdict: SHIP\n")
        (self.ws / "work" / "evaluation" / "data-split.yaml").write_bytes(b"\xff\xfe not utf-8")
        self.assertEqual(self.hook(stop(self.ws)), {}, "the guard's own failure never holds the turn")

    def test_configured_commands_run_from_the_plugin_root(self) -> None:
        claude = json.loads((SKILL / "hooks" / "hooks.json").read_text())["hooks"]["Stop"]
        [[hook]] = [entry["hooks"] for entry in claude]
        self.assertEqual(hook["command"], 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/finish-guard"')
        cmd = [SH, "-c", hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(SKILL))]
        self.assertEqual(self.hook(stop(self.ws), cmd)["decision"], "block")
        manifest = json.loads((SKILL / ".cursor-plugin" / "plugin.json").read_text())
        [cursor] = json.loads((SKILL / manifest["hooks"]).read_text())["hooks"]["stop"]
        self.assertEqual(cursor["loop_limit"], 1, "Cursor's own cap: one follow-up per turn")
        cmd = [SH, "-c", cursor["command"].replace("${CURSOR_PLUGIN_ROOT}", str(SKILL))]
        self.assertIn("BUILD UNFINISHED", self.hook(cursor_stop(self.ws), cmd)["followup_message"])

    def test_the_send_backs_stop_at_max_finish_sendbacks(self) -> None:
        rules = json.loads((SKILL / "references" / "verdict-rules.json").read_text())
        self.assertEqual(rules["max_finish_sendbacks"], 3)
        other = self.build(self.root / "other")
        log = self.ws / "work" / "experiments" / "finish-guard.jsonl"
        for n in range(1, 4):
            self.assertEqual(self.hook(stop(self.ws))["decision"], "block", f"send-back {n}")
            self.assertEqual(self.hook(stop(self.ws, active=True)), {}, "the stop after a send-back ends the turn")
        out = subprocess.run([sys.executable, "-B", str(HOOK)], input=json.dumps(stop(self.ws)), capture_output=True,
                             text=True, timeout=60, env=ENV)
        self.assertEqual((out.returncode, json.loads(out.stdout)), (0, {}), "the fourth stop ends the build")
        self.assertIn(f"{self.ws} was sent back 3 times (max_finish_sendbacks 3); the build ends unfinished", out.stderr)
        self.assertEqual(len(log.read_text().splitlines()), 3, "the allowed stop is not logged as a send-back")
        self.assertEqual(self.hook(cursor_stop(self.ws)), {}, "the cap holds on every host")
        self.assertEqual(self.hook(stop(other))["decision"], "block", "the count is per build workspace")
        finish = subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / "check_gate.py"), "--finish",
                                 "--workspace", str(self.ws)], capture_output=True, text=True, timeout=60, env=ENV)
        self.assertNotEqual(finish.returncode, 0, "check_gate.py --finish still fails the unfinished build")

    def test_phases_1_and_2_neither_send_back_nor_count(self) -> None:
        """Before output/<skill>/SKILL.md exists the build is interviewing or researching: no send-back, no count."""
        early = self.root / "early"
        (early / "work" / "evaluation").mkdir(parents=True)
        (early / "work" / "manifest.yaml").write_text("gold_standards: []\n")
        for _ in range(5):
            self.assertEqual(self.hook(stop(early)), {})
        self.assertFalse((early / "work" / "experiments" / "finish-guard.jsonl").exists())
        (early / "output" / "demo").mkdir(parents=True)
        (early / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n")
        self.assertEqual(self.hook(stop(early))["decision"], "block", "Phase 3 on: the full allowance")

    def test_without_any_python_the_turn_ends(self) -> None:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "cat").symlink_to(shutil.which("cat"))
        out = subprocess.run([SH, LAUNCHER.as_posix()], input=json.dumps(stop(self.ws)), capture_output=True,
                             text=True, timeout=30, env={**os.environ, "PATH": str(bin_dir)})
        self.assertEqual((out.returncode, json.loads(out.stdout)), (0, {}))
        self.assertIn("no Python 3.9+ found", out.stderr)


def agent(cwd: Path, background: object = "absent", tool: str = "Agent") -> dict:
    tool_input = {"description": "Phase 3 worker", "subagent_type": "general-purpose", "prompt": "p"}
    if background != "absent":
        tool_input["run_in_background"] = background
    return {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(cwd), "tool_name": tool,
            "tool_input": tool_input, "tool_use_id": "t"}


def cursor_task(root: Path, background: object = None, wrapped: bool = False) -> dict:
    args = {"description": "Research", "subagent_type": "explore", "prompt": "p"}
    if wrapped:  # the shape Cursor's transcripts record: CallDynamicTool {toolName: Task, arguments, run_in_background}
        tool, tool_input = "CallDynamicTool", {"namespace": "cursor", "toolName": "Task", "arguments": args}
    else:
        tool, tool_input = "Task", args
    if background is not None:
        tool_input["run_in_background"] = background
    return {"hook_event_name": "preToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(root),
            "workspace_roots": [str(root)]}


class SubagentGuardTest(unittest.TestCase):
    """finish_guard.py --subagent: a background subagent is denied inside a build (D1: a headless session stops
    background subagents 10 minutes after its last turn), allowed in the foreground and outside a build."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "ws"
        (self.ws / "work").mkdir(parents=True)
        (self.ws / "work" / "manifest.yaml").write_text("gold_standards: []\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def guard(self, payload: object, cmd: list[str] | None = None) -> subprocess.CompletedProcess:
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(cmd or [sys.executable, "-B", str(HOOK), "--subagent"], input=data, capture_output=True,
                              text=True, timeout=60, env=ENV)

    def test_a_background_agent_is_denied_in_a_build_from_phase_1_on(self) -> None:
        for background in (True, "absent"):
            with self.subTest(run_in_background=background):
                out = self.guard(agent(self.ws, background))
                self.assertEqual(out.returncode, 2, "Claude Code's Agent tool defaults to the background")
                self.assertIn("dispatch phase workers and panel roles in the foreground (run_in_background: false)",
                              out.stderr)
                self.assertIn("several Agent calls in one message", out.stderr)
        nested = self.root / "project" / "builds" / "demo" / "work" / "evaluation"
        nested.mkdir(parents=True)
        self.assertEqual(self.guard(agent(self.root / "project", True)).returncode, 2, "a build under builds/")

    def test_a_foreground_agent_is_allowed(self) -> None:
        out = self.guard(agent(self.ws, False))
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        self.assertEqual(self.guard(agent(self.ws, "absent", tool="Task")).returncode, 0,
                         "a legacy Task tool runs in the foreground unless asked")
        self.assertEqual(self.guard(agent(self.ws, True, tool="Task")).returncode, 2)
        self.assertEqual(self.guard(agent(self.ws, True, tool="Bash")).returncode, 0, "not a subagent tool")

    def test_outside_a_build_workspace_anything_goes(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        self.assertEqual(self.guard(agent(plain, True)).returncode, 0)
        self.assertEqual(self.guard(agent(plain)).returncode, 0)

    def test_cursor_denies_only_an_explicit_background_task(self) -> None:
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                deny = json.loads(self.guard(cursor_task(self.ws, True, wrapped)).stdout)
                self.assertEqual(deny["permission"], "deny")
                self.assertIn("run_in_background: false", deny["agent_message"])
                for background in (None, False):
                    self.assertEqual(json.loads(self.guard(cursor_task(self.ws, background, wrapped)).stdout),
                                     {"permission": "allow"})
        wrapped = cursor_task(self.ws, wrapped=True)
        wrapped["tool_input"]["arguments"]["run_in_background"] = True
        self.assertEqual(json.loads(self.guard(wrapped).stdout)["permission"], "deny", "the flag inside arguments")
        plain = self.root / "plain"
        plain.mkdir()
        self.assertEqual(json.loads(self.guard(cursor_task(plain, True)).stdout), {"permission": "allow"})

    def test_unreadable_input_fails_open(self) -> None:
        for payload in ("not json", "[1, 2]", "", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent",
                                                            "tool_input": {}, "cwd": 7})):
            with self.subTest(payload=payload):
                out = self.guard(payload)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertIn(json.loads(out.stdout or "{}"), ({}, {"permission": "allow"}))

    def test_configured_commands_run_from_the_plugin_root(self) -> None:
        [entry] = [e for e in json.loads((SKILL / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
                   if e["matcher"] == "Agent|Task"]
        [hook] = entry["hooks"]
        self.assertEqual(hook["command"], 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/finish-guard" --subagent')
        out = self.guard(agent(self.ws), [SH, "-c", hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(SKILL))])
        self.assertEqual(out.returncode, 2, out.stderr)
        manifest = json.loads((SKILL / ".cursor-plugin" / "plugin.json").read_text())
        [cursor] = json.loads((SKILL / manifest["hooks"]).read_text())["hooks"]["preToolUse"]
        self.assertEqual(cursor["matcher"], "Task|CallDynamicTool")
        cmd = [SH, "-c", cursor["command"].replace("${CURSOR_PLUGIN_ROOT}", str(SKILL))]
        self.assertEqual(json.loads(self.guard(cursor_task(self.ws, True), cmd).stdout)["permission"], "deny")

    def test_without_any_python_the_subagent_starts(self) -> None:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "cat").symlink_to(shutil.which("cat"))
        env = {**os.environ, "PATH": str(bin_dir)}
        for payload, reply in ((agent(self.ws, True), {}), (cursor_task(self.ws, True), {"permission": "allow"})):
            out = subprocess.run([SH, LAUNCHER.as_posix(), "--subagent"], input=json.dumps(payload),
                                 capture_output=True, text=True, timeout=30, env=env)
            self.assertEqual((out.returncode, json.loads(out.stdout)), (0, reply))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class OpenCodeFinishGuardTest(unittest.TestCase):
    """hooks/opencode-finish-guard.js under node, with a stub OpenCode client."""

    PLUGIN = (SKILL / "hooks" / "opencode-finish-guard.js").as_uri()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = FinishGuardTest.build(Path(self._tmp.name) / "ws")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def idle(self, times: int, session: str = "{ data: { id: \"s1\" } }") -> list[str]:
        """The prompts the plugin sends over `times` session.idle events of one session; `session` is the JS
        expression `client.session.get` resolves to (or a `throw` statement)."""
        out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", textwrap.dedent(f"""
            import {{ FinishGuard }} from {json.dumps(self.PLUGIN)};
            const prompts = [];
            const client = {{ session: {{
              prompt: async (req) => {{ prompts.push(req.body.parts[0].text); }},
              get: async () => {{ {session if session.startswith("throw") else "return " + session}; }},
            }} }};
            const hooks = await FinishGuard({{ client, directory: {json.dumps(str(self.ws))} }});
            for (let i = 0; i < {times}; i++) {{
              await hooks.event({{ event: {{ type: "session.idle", properties: {{ sessionID: "s1" }} }} }});
            }}
            await hooks.event({{ event: {{ type: "session.updated", properties: {{ sessionID: "s1" }} }} }});
            console.log(JSON.stringify(prompts));
        """)], capture_output=True, text=True, timeout=60, env=ENV)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_prompts_once_then_lets_the_session_idle(self) -> None:
        prompts = self.idle(2)
        self.assertEqual(len(prompts), 1, "the second idle ends the turn whatever the guard says")
        self.assertIn("BUILD UNFINISHED: no BENCHMARK.md", prompts[0])
        self.assertEqual(len(self.idle(3)), 2, "a later turn is checked again")

    def test_a_subagent_child_session_gets_no_prompt(self) -> None:
        self.assertEqual(self.idle(1, '{ data: { id: "s1", parentID: "p0" } }'), [], "a worker's child session")
        self.assertEqual(len(self.idle(1, 'throw new Error("no such session")')), 1, "lookup failed: top-level")

    def test_a_finished_build_gets_no_prompt(self) -> None:
        FinishGuardTest.finish(self.ws)
        self.assertEqual(self.idle(1), [])

    def test_every_export_is_a_plugin(self) -> None:
        out = subprocess.run([shutil.which("node"), "--input-type=module", "-e",
                              f"const m = await import({json.dumps(self.PLUGIN)}); console.log(Object.keys(m).join())"],
                             capture_output=True, text=True, timeout=60, env=ENV)
        self.assertEqual(out.stdout.strip(), "FinishGuard")


if __name__ == "__main__":
    unittest.main()
