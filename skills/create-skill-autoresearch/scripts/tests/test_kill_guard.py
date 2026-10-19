"""Offline tests for hooks/kill_guard.py (the PreToolUse kill guard) and hooks/hooks.json.

Each case feeds the hook the JSON Claude Code sends on stdin and checks its exit code: 2 blocks
the Bash call (reason on stderr), 0 lets it through.

    python3 -B -m unittest discover -s <skill>/scripts/tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
HOOK = SKILL / "hooks" / "kill_guard.py"

ALLOWED = [
    "python3 scripts/run_owned.py stop gate",
    "python3 scripts/gate.py --stop",
    "kill 12345",
    "kill -TERM -- -4242",
    "kill -0 $(cat work/pids/gate.pid)",
    "ps -p 4242 -o pgid=",
    "pgrep -f gate.py",
    "echo $(pidof launchd)",
    "ls skills/ && cat SKILL.md",
    "git log --oneline -5",
    "npm test 2>&1 | tail -5",
]

BLOCKED = [
    "pkill -f gate.py",
    "pkill -9 -f 'claude plugin eval'",
    "killall python3",
    "/usr/bin/pkill -f eval",
    "sudo killall -9 node",
    "sleep 1; pkill -f run-case",
    "cd work && pkill -f \"until grep -q\"",
    "pgrep -f gate.py | xargs kill",
    "pgrep -f gate.py | xargs -I{} kill -9 {}",
    "pgrep -f eval | xargs -r kill -TERM",
    "ps aux | grep plugin | awk '{print $2}' | xargs kill",
    "pidof python3 | xargs kill",
    "kill $(pgrep -f gate.py)",
    "kill -9 $( pgrep -f 'plugin eval' )",
    "kill `pgrep -f gate`",
    "for p in $(pgrep -f run-case); do kill $p; done",
    "bash -c 'pkill -f gate'",
]

QUOTING_AND_WHITESPACE = [
    "p'k'ill -f gate",
    '"pkill" -f gate',
    "\\pkill -f gate",
    "pkill\t-f\tgate",
    "  killall   python3  ",
    "kill \\\n  $(pgrep -f gate)",
    "pgrep -f gate |\txargs   kill",
    "k\"ill\"all node",
]


def run_hook(payload: object) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, "-B", str(HOOK)], input=data, capture_output=True, text=True,
                          timeout=30, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def bash(command: str) -> dict:
    return {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command, "description": "test"}, "tool_use_id": "t"}


class KillGuardTest(unittest.TestCase):
    def assert_exit(self, command: str, code: int) -> subprocess.CompletedProcess:
        out = run_hook(bash(command))
        self.assertEqual(out.returncode, code, f"{command!r}: {out.stderr}")
        return out

    def test_allowed_commands_pass(self) -> None:
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assertEqual(self.assert_exit(command, 0).stderr, "")

    def test_each_blocked_pattern_is_blocked_with_the_run_owned_message(self) -> None:
        for command in BLOCKED:
            with self.subTest(command=command):
                stderr = self.assert_exit(command, 2).stderr
                self.assertIn("Use scripts/run_owned.py stop <name>; pattern kills hit unrelated processes.", stderr)

    def test_quoting_and_whitespace_variants_are_blocked(self) -> None:
        for command in QUOTING_AND_WHITESPACE:
            with self.subTest(command=command):
                self.assert_exit(command, 2)

    def test_other_tools_and_unreadable_input_pass(self) -> None:
        self.assertEqual(run_hook({"tool_name": "Write", "tool_input": {"content": "pkill -f x"}}).returncode, 0)
        self.assertEqual(run_hook("not json").returncode, 0)
        self.assertEqual(run_hook({"tool_name": "Bash", "tool_input": {}}).returncode, 0)


def cursor(command: str) -> dict:
    return {"conversation_id": "c", "generation_id": "g", "hook_event_name": "beforeShellExecution",
            "cursor_version": "2026.10.16", "workspace_roots": ["/tmp/ws"], "command": command,
            "cwd": "/tmp/ws", "sandbox": False}


class CursorContractTest(unittest.TestCase):
    """Cursor's beforeShellExecution: always a JSON permission on stdout, exit 0 (a permission hook that
    prints nothing blocks the command)."""

    def reply(self, command: str) -> dict:
        out = run_hook(cursor(command))
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_allowed_commands_get_an_explicit_allow(self) -> None:
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assertEqual(self.reply(command), {"permission": "allow"})

    def test_blocked_and_variant_commands_are_denied_with_the_message_for_both_audiences(self) -> None:
        for command in BLOCKED + QUOTING_AND_WHITESPACE:
            with self.subTest(command=command):
                reply = self.reply(command)
                self.assertEqual(reply["permission"], "deny")
                self.assertIn("run_owned.py stop <name>", reply["agent_message"])
                self.assertEqual(reply["user_message"], reply["agent_message"])


class CodexContractTest(unittest.TestCase):
    """Codex PreToolUse sends the Claude shape (tool_input.command) and honours exit 2 + stderr."""

    def test_codex_shell_call(self) -> None:
        event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/tmp/ws",
                 "tool_input": {"command": "pkill -f eval"}}
        out = run_hook(event)
        self.assertEqual(out.returncode, 2)
        self.assertIn("pattern kills hit unrelated processes", out.stderr)
        event["tool_input"]["command"] = "ls"
        self.assertEqual((run_hook(event).returncode, run_hook(event).stdout), (0, ""))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class OpenCodePluginTest(unittest.TestCase):
    """hooks/opencode-kill-guard.js under node: tool.execute.before throws for a pattern kill on bash."""

    def call(self, tool: str, command: str) -> str:
        script = textwrap.dedent(f"""
            import {{ KillGuard }} from {json.dumps((SKILL / 'hooks' / 'opencode-kill-guard.js').as_uri())};
            const hooks = await KillGuard({{}});
            try {{
              await hooks["tool.execute.before"]({{ tool: {json.dumps(tool)} }}, {{ args: {{ command: {json.dumps(command)} }} }});
              console.log("allowed");
            }} catch (error) {{ console.log("blocked: " + error.message); }}
        """)
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                             timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_blocks_pattern_kills_and_lets_the_rest_through(self) -> None:
        self.assertTrue(self.call("bash", "pkill -f gate.py").startswith("blocked: Blocked by create-skill"))
        self.assertTrue(self.call("bash", "kill $(pgrep -f eval)").startswith("blocked:"))
        self.assertEqual(self.call("bash", "python3 scripts/run_owned.py stop gate"), "allowed")
        self.assertEqual(self.call("read", "pkill -f gate.py"), "allowed", "only bash calls are checked")


class CursorPluginTest(unittest.TestCase):
    def test_manifest_points_at_a_cursor_hooks_file_that_runs_the_guard(self) -> None:
        manifest = json.loads((SKILL / ".cursor-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "create-skill-autoresearch")
        frontmatter_version = next(line.split(":", 1)[1].strip() for line in (SKILL / "SKILL.md").read_text().splitlines()
                                   if line.startswith("version:"))
        self.assertEqual(manifest["version"], frontmatter_version)
        config = json.loads((SKILL / manifest["hooks"]).read_text())
        self.assertEqual(config["version"], 1)
        [hook] = config["hooks"]["beforeShellExecution"]
        self.assertEqual(hook["command"], 'python3 "${CURSOR_PLUGIN_ROOT}/hooks/kill_guard.py"')
        self.assertNotEqual(manifest["hooks"], "hooks/hooks.json", "hooks/hooks.json is Claude's format")


class HooksJsonTest(unittest.TestCase):
    def test_bash_pretooluse_hook_runs_the_guard_from_the_plugin_root(self) -> None:
        config = json.loads((SKILL / "hooks" / "hooks.json").read_text())
        [entry] = config["hooks"]["PreToolUse"]
        self.assertEqual(entry["matcher"], "Bash")
        [hook] = entry["hooks"]
        self.assertEqual((hook["type"], hook["command"]), ("command", "python3"))
        self.assertEqual(hook["args"], ["${CLAUDE_PLUGIN_ROOT}/hooks/kill_guard.py"])
        self.assertTrue((SKILL / hook["args"][0].replace("${CLAUDE_PLUGIN_ROOT}/", "")).is_file())


if __name__ == "__main__":
    unittest.main()
