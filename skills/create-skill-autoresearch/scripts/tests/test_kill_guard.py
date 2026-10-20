"""Offline tests for hooks/kill_guard.py (the shell-command kill guard), its launcher hooks/kill-guard,
the OpenCode plugin and the hook configs.

Each case feeds the hook the JSON a host sends on stdin and checks its answer: Claude Code and Codex
exit 2 to block the call (reason on stderr) and 0 to let it through; Cursor always gets JSON.

    python3 -B -m unittest discover -s <skill>/scripts/tests -v
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

SKILL = Path(__file__).resolve().parents[2]
HOOK = SKILL / "hooks" / "kill_guard.py"
LAUNCHER = SKILL / "hooks" / "kill-guard"
WINDOWS = os.name == "nt"
SH = shutil.which("sh") if WINDOWS else "/bin/sh"

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
    'kill %1 && find . -name "*.log"',
    "docker ps | grep x; kill 123",
    "ps aux | grep gate\nkill 4242",
    "pid=$(cat work/pids/gate.pid); kill $pid",
    "kill 4242 &\nps -p 4242",
    "echo a & echo b",
    # A listing and a kill of literal PIDs, nothing kept; this hook's own file names.
    "ps aux | head; kill 1234",
    "ps -o pgid= -p 4242; kill -TERM -- -4242",
    "pgrep -f gate.py\nkill -s KILL 4242 %1",
    "ps aux | grep gate && sudo kill -9 4242",
    "ps aux | grep kill-guard",
    "ps aux | grep kill_guard",
    "pgrep -f gate > /tmp/p; cat /tmp/p",
    "pgrep -f gate 2>/dev/null; ps -p 4242 2>&1; kill 4242",
]

# Round 5: taint lost across renames, copies and redirects. A lister and a kill in one call are blocked unless
# every kill names literal PIDs and nothing keeps the listing; these were allowed in rounds 2-4.
LISTING_AND_KILL = [
    "pgrep -f gate.py > /tmp/a; mv /tmp/a /tmp/b; xargs kill < /tmp/b",
    "pgrep -f x > f; cp f g; kill $(cat g)",
    "pgrep -f x > f; kill $(cat f)",
    "pgrep -f x &> f\nmv f g\nkill `cat g`",
    "ps aux | awk '{print $2}' > f; sed -n 1p f > g; xargs -a g kill",
    "pgrep -f x | sort > f; while read p; do kill $p; done < f",
    "ps aux | head; kill $PID",
    "ps aux | grep gate; pid=$(cat work/pids/gate.pid); kill $pid",
    "ps aux > /tmp/ps.txt; kill 4242",
    "pgrep x; cat f | kill 4242",
    "ps aux; eval kill $(cat f)",
    "Get-Process node | Out-File p.txt; Get-Content p.txt | Stop-Process",
    "tasklist > t.txt; taskkill /PID 4242",
    "$p = Get-Process claude; Stop-Process -Id $pid",
]

WINDOWS_ALLOWED = [
    "taskkill /PID 4242 /F",
    "taskkill /F /T /PID 4242",
    "Stop-Process -Id 4242 -Force",
    "Stop-Process 4242",
    "Get-Process -Id 4242",
    'tasklist /FI "IMAGENAME eq python.exe"',
    "kill -n 9 4242",
    "Get-Process -Id 4242; Stop-Process -Id 4242 -Force",
    "tasklist /FI \"PID eq 4242\"; taskkill /PID 4242 /F",
]

WINDOWS_BLOCKED = [
    "taskkill /IM python.exe /F",
    'taskkill /F /FI "IMAGENAME eq node.exe"',
    "taskkill -im node.exe",
    "TASKKILL /im claude.exe /t",
    "C:\\Windows\\System32\\taskkill.exe /IM python.exe",
    "Stop-Process -Name python",
    "Stop-Process -n node -Force",
    "spps -Name claude",
    "kill -Name node",
    "Get-Process python | Stop-Process",
    "gps node | spps -Force",
    "(Get-Process node).Kill()",
    "Get-Process | Where-Object { $_.Name -like '*py*' } | ForEach-Object { $_.Kill() }",
    "$p = Get-Process claude; Stop-Process -InputObject $p",
    "wmic process where \"name='python.exe'\" delete",
    'wmic process where name="node.exe" call terminate',
    "Get-CimInstance Win32_Process -Filter \"Name = 'x.exe'\" | Invoke-CimMethod -MethodName Terminate",
    "pkill.exe -f gate",
    "Stop-Pro`cess -Name node",
    "t^askkill /im node.exe",
    "Get-Process node | kill",
    "$p = Get-Process claude; $p.Kill()",
    "kill (Get-Process node).Id",
    "$procs = Get-Process node; foreach ($x in $procs) { Stop-Process -Id $x.Id }",
    "$P = gps claude; $p | Stop-Process",
]

BLOCKED = [
    "lsof -ti :8080 | xargs kill",
    "fuser -k 8080/tcp",
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
    "kill $(command pgrep -f gate.py)",
    "kill $(sudo pgrep -f gate.py)",
    "kill $(env LC_ALL=C pgrep -f gate.py)",
    "exec kill $(builtin command pgrep x)",
    "xargs kill < <(pgrep -f gate)",
    "while read p; do kill $p; done < <(pgrep -f eval)",
    "pgrep -f eval | while read p; do kill $p; done",
    "pids=`pgrep -f gate`; kill -9 ${pids}",
    "mapfile -t pids < <(pgrep -f gate); kill \"${pids[@]}\"",
    # A listing passed through a file.
    "pgrep -f gate > /tmp/p; kill $(cat /tmp/p)",
    "ps aux | grep eval | awk '{print $2}' >> pids.txt\nxargs kill < pids.txt",
    "pgrep -f gate | tee /tmp/p; cat /tmp/p | xargs kill",
    "pgrep -f gate > /tmp/p; pids=$(cat /tmp/p); kill $pids",
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
    # Copilot review on agent-skills#24: bash deletes a backslash-newline pair, joining the halves.
    "pki\\\nll -f gate",
    "kil\\\nl $(pgrep -f gate)",
]

# Copilot review on agent-skills#24: a pipe or a substitution split across lines slipped past the
# per-line match.
MULTILINE = [
    "kill $(\n  pgrep -f x\n)",
    "pgrep -f x |\n  xargs kill",
    "pgrep -f x\\\n | xargs kill -9",
    "kill -TERM `\npgrep -f gate`",
    "pids=$(pgrep -f gate.py)\nkill $pids",
    "ps aux |\n grep eval |\n awk '{print $2}' |\n xargs kill",
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

    def test_a_listing_and_a_kill_in_one_call_are_blocked_however_the_pids_travel(self) -> None:
        for command in LISTING_AND_KILL:
            with self.subTest(command=command):
                self.assertIn("in a call that lists no processes", self.assert_exit(command, 2).stderr)

    def test_quoting_and_whitespace_variants_are_blocked(self) -> None:
        for command in QUOTING_AND_WHITESPACE:
            with self.subTest(command=command):
                self.assert_exit(command, 2)

    def test_multiline_pipes_and_substitutions_are_blocked(self) -> None:
        for command in MULTILINE:
            with self.subTest(command=command):
                self.assert_exit(command, 2)
                self.assertEqual(json.loads(run_hook(cursor(command)).stdout)["permission"], "deny")

    def test_multiline_commands_without_a_pattern_kill_pass(self) -> None:
        for command in ("ls\npython3 scripts/run_owned.py stop gate", "pgrep -f gate.py\necho done",
                        "git status |\n  head -5", "kill 4242\necho stopped"):
            with self.subTest(command=command):
                self.assert_exit(command, 0)

    def test_windows_pid_kills_pass_and_name_kills_are_blocked(self) -> None:
        for command in WINDOWS_ALLOWED:
            with self.subTest(command=command):
                self.assert_exit(command, 0)
        for command in WINDOWS_BLOCKED:
            with self.subTest(command=command):
                self.assertIn("taskkill /PID <pid>", self.assert_exit(command, 2).stderr)

    def test_powershell_tool_calls_are_checked(self) -> None:
        event = bash("Stop-Process -Name node") | {"tool_name": "PowerShell"}
        self.assertEqual(run_hook(event).returncode, 2)
        event["tool_input"]["command"] = "Stop-Process -Id 4242"
        self.assertEqual(run_hook(event).returncode, 0)

    def test_other_tools_and_unreadable_input_pass(self) -> None:
        self.assertEqual(run_hook({"tool_name": "Write", "tool_input": {"content": "pkill -f x"}}).returncode, 0)
        self.assertEqual(run_hook({"tool_name": "Bash", "tool_input": {}}).returncode, 0)

    def test_unreadable_input_answers_allow_for_cursor(self) -> None:
        for payload in ("not json", "", "[1, 2]"):
            with self.subTest(payload=payload):
                out = run_hook(payload)
                self.assertEqual(out.returncode, 0)
                self.assertEqual(json.loads(out.stdout), {"permission": "allow"},
                                 "Cursor blocks a permission hook whose stdout is empty or not JSON")


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
        for command in ALLOWED + WINDOWS_ALLOWED:
            with self.subTest(command=command):
                self.assertEqual(self.reply(command), {"permission": "allow"})

    def test_blocked_and_variant_commands_are_denied_with_the_message_for_both_audiences(self) -> None:
        for command in BLOCKED + QUOTING_AND_WHITESPACE + WINDOWS_BLOCKED + LISTING_AND_KILL:
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


def python_only_as(directory: Path, name: str) -> dict[str, str]:
    """An environment whose PATH holds just `cat` and this test's Python, called `name`, plus a python3
    that behaves like the Microsoft Store stub (exit 9009) when `name` is not python3."""
    directory.mkdir(exist_ok=True)
    (directory / name).symlink_to(sys.executable)
    (directory / "cat").symlink_to(shutil.which("cat"))
    if name != "python3":
        (directory / "python3").write_text("#!/bin/sh\necho 'Python was not found' >&2\nexit 9009\n")
        (directory / "python3").chmod(0o755)
    return {**os.environ, "PATH": str(directory), "PYTHONDONTWRITEBYTECODE": "1"}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class OpenCodePluginTest(unittest.TestCase):
    """hooks/opencode-kill-guard.js under node: tool.execute.before throws for a pattern kill on bash."""

    PLUGIN = (SKILL / "hooks" / "opencode-kill-guard.js").as_uri()

    def node(self, script: str, env: dict[str, str] | None = None) -> str:
        out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", textwrap.dedent(script)],
                             capture_output=True, text=True, timeout=60,
                             env=env or {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def call(self, tool: str, command: str, env: dict[str, str] | None = None) -> str:
        return self.node(f"""
            import {{ KillGuard }} from {json.dumps(self.PLUGIN)};
            const hooks = await KillGuard({{}});
            try {{
              await hooks["tool.execute.before"]({{ tool: {json.dumps(tool)} }}, {{ args: {{ command: {json.dumps(command)} }} }});
              console.log("allowed");
            }} catch (error) {{ console.log("blocked: " + error.message); }}
        """, env)

    def test_blocks_pattern_kills_and_lets_the_rest_through(self) -> None:
        self.assertTrue(self.call("bash", "pkill -f gate.py").startswith("blocked: Blocked by create-skill"))
        self.assertTrue(self.call("bash", "kill $(pgrep -f eval)").startswith("blocked:"))
        self.assertTrue(self.call("bash", "taskkill /IM node.exe /F").startswith("blocked:"))
        self.assertEqual(self.call("bash", "python3 scripts/run_owned.py stop gate"), "allowed")
        self.assertEqual(self.call("read", "pkill -f gate.py"), "allowed", "only bash calls are checked")

    def test_every_export_passes_opencodes_plugin_loader(self) -> None:
        """OpenCode v1.19 (packages/opencode/src/plugin/index.ts getLegacyPlugins) treats every export
        of a plugin module as a plugin: a function, or an object with a `server` function, whose call
        returns the hooks object. Anything else breaks tool dispatch or throws."""
        verdicts = json.loads(self.node(f"""
            const mod = await import({json.dumps(self.PLUGIN)});
            const verdicts = {{}};
            for (const [name, value] of Object.entries(mod)) {{
              const factory = typeof value === "function" ? value : value?.server;
              if (typeof factory !== "function") {{ verdicts[name] = "not a plugin"; continue; }}
              const hooks = await factory({{}});
              verdicts[name] = hooks && typeof hooks === "object" && !Array.isArray(hooks) ? "plugin" : "no hooks";
            }}
            console.log(JSON.stringify(verdicts));
        """))
        self.assertEqual(verdicts, {"KillGuard": "plugin"})

    @unittest.skipIf(WINDOWS, "symlinks need privileges on Windows; the real PATH run covers py -3 there")
    def test_runs_the_guard_where_python3_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = python_only_as(Path(tmp), "python")
            self.assertTrue(self.call("bash", "pkill -f gate.py", env).startswith("blocked:"))
            self.assertEqual(self.call("bash", "ls", env), "allowed")


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
        self.assertEqual(hook["command"], 'sh "${CURSOR_PLUGIN_ROOT}/hooks/kill-guard"')
        self.assertNotEqual(manifest["hooks"], "hooks/hooks.json", "hooks/hooks.json is Claude's format")

    def test_hosts_md_labels_the_sh_plugin_hook_unsupported_on_native_windows(self) -> None:
        """Cursor runs hooks through PowerShell on native Windows and fails open when `sh` cannot start."""
        hosts = (SKILL / "references" / "hosts.md").read_text(encoding="utf-8")
        self.assertIn("**The Cursor plugin hook is unsupported on native Windows.**", hosts)
        self.assertIn("py -3 <skill-dir>\\hooks\\kill_guard.py", hosts)


def run_configured(command: str, variable: str, payload: dict) -> subprocess.CompletedProcess:
    """Run a hook's configured shell-form command the way a host does: the plugin-root placeholder
    expanded to the skill directory, through `sh -c` (Git Bash's on Windows), the event on stdin."""
    return subprocess.run([SH, "-c", command.replace("${" + variable + "}", str(SKILL))],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=30,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


class HooksJsonTest(unittest.TestCase):
    def test_bash_pretooluse_hook_runs_the_guard_from_the_plugin_root(self) -> None:
        config = json.loads((SKILL / "hooks" / "hooks.json").read_text())
        [entry] = config["hooks"]["PreToolUse"]
        self.assertEqual(entry["matcher"], "Bash|PowerShell", "Claude Code on Windows runs shell commands as PowerShell")
        [hook] = entry["hooks"]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["command"], 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/kill-guard"',
                         "one shell-form string, no args: portable across CLI versions and hosts")
        self.assertNotIn("args", hook)

    def test_both_claude_hooks_fail_open(self) -> None:
        """Claude Code hooks doc: on Windows without Git Bash, a failing shell-form PreToolUse hook with
        `onFailure` unset can block the tool call; "continue" lets it through (v2.1.295+)."""
        config = json.loads((SKILL / "hooks" / "hooks.json").read_text())["hooks"]
        for event in ("PreToolUse", "Stop"):
            with self.subTest(event=event):
                [entry] = config[event]
                self.assertEqual([h.get("onFailure") for h in entry["hooks"]], ["continue"])

    def test_configured_claude_command_executes_and_blocks(self) -> None:
        [hook] = json.loads((SKILL / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"]
        blocked = run_configured(hook["command"], "CLAUDE_PLUGIN_ROOT", bash("pkill -f gate.py"))
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn("pattern kills hit unrelated processes", blocked.stderr)
        self.assertEqual(run_configured(hook["command"], "CLAUDE_PLUGIN_ROOT", bash("ls")).returncode, 0)

    def test_configured_cursor_command_executes_and_answers_json(self) -> None:
        manifest = json.loads((SKILL / ".cursor-plugin" / "plugin.json").read_text())
        [hook] = json.loads((SKILL / manifest["hooks"]).read_text())["hooks"]["beforeShellExecution"]
        deny = run_configured(hook["command"], "CURSOR_PLUGIN_ROOT", cursor("killall node"))
        self.assertEqual((deny.returncode, json.loads(deny.stdout)["permission"]), (0, "deny"))
        allow = run_configured(hook["command"], "CURSOR_PLUGIN_ROOT", cursor("ls"))
        self.assertEqual(json.loads(allow.stdout), {"permission": "allow"})


class LauncherTest(unittest.TestCase):
    """hooks/kill-guard picks a Python 3 itself, so the hooks run where `python3` is not on PATH."""

    def launch(self, payload: dict, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run([SH, LAUNCHER.as_posix()], input=json.dumps(payload), capture_output=True, text=True,
                              timeout=30, env=env)

    def test_runs_with_the_python_on_path(self) -> None:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        self.assertEqual(self.launch(bash("taskkill /IM node.exe"), env).returncode, 2)
        self.assertEqual(json.loads(self.launch(cursor("ls"), env).stdout), {"permission": "allow"})

    def test_runs_with_python3_taken_off_path(self) -> None:
        dirs = [d for d in os.environ["PATH"].split(os.pathsep)
                if d and not any(os.path.exists(os.path.join(d, n)) for n in ("python3", "python3.exe"))]
        path = os.pathsep.join(dirs)
        if not any(shutil.which(n, path=path) for n in ("python", "py")):
            self.skipTest("no python or py left once python3 is off PATH")
        env = {**os.environ, "PATH": path, "PYTHONDONTWRITEBYTECODE": "1"}
        blocked = self.launch(bash("taskkill /IM node.exe"), env)
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertEqual(json.loads(self.launch(cursor("ls"), env).stdout), {"permission": "allow"})

    @unittest.skipIf(WINDOWS, "symlinks need privileges on Windows; the python3-off-PATH run covers it there")
    def test_falls_back_to_python_past_a_store_stub_python3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = python_only_as(Path(tmp), "python")
            blocked = self.launch(bash("pkill -f gate"), env)
            self.assertEqual(blocked.returncode, 2, blocked.stderr)
            self.assertIn("pattern kills hit unrelated processes", blocked.stderr)
            self.assertEqual(json.loads(self.launch(cursor("killall node"), env).stdout)["permission"], "deny")

    @unittest.skipIf(WINDOWS, "symlinks need privileges on Windows")
    def test_without_any_python_the_call_is_allowed_and_cursor_gets_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "cat").symlink_to(shutil.which("cat"))
            env = {**os.environ, "PATH": tmp}
            claude = self.launch(bash("pkill -f gate"), env)
            self.assertEqual((claude.returncode, claude.stdout), (0, ""))
            self.assertIn("no Python 3.9+ found", claude.stderr)
            self.assertEqual(json.loads(self.launch(cursor("pkill -f gate"), env).stdout), {"permission": "allow"})


if __name__ == "__main__":
    unittest.main()
