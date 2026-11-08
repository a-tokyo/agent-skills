"""Offline tests for hooks/freeze_guard.py (the freeze guard), its launcher hooks/freeze-guard, its shell half in
hooks/kill_guard.py, the OpenCode plugin's write tools and the hook configs (r22: r21 Haiku builders fixed panel
findings in ordinary commits after the panel, which ended the build UNSHIPPED).

Each case feeds a hook the JSON a host sends on stdin: Claude Code exits 2 to deny (reason on stderr) and 0 to
allow; Cursor always gets JSON. Build fixtures come from test_fix_cycle.py.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from test_fix_cycle import HIGH, SCENARIO, FixCycleFixture

SKILL = Path(__file__).resolve().parents[1]
FREEZE = SKILL / "hooks" / "freeze_guard.py"
KILL = SKILL / "hooks" / "kill_guard.py"


def hook(path: Path, event: dict, **env: str) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k not in ("CSA_EXECUTOR", "CSA_PROFILE")}
    return subprocess.run([sys.executable, "-B", str(path)], input=json.dumps(event), capture_output=True, text=True,
                          timeout=60, env={**full, "PYTHONDONTWRITEBYTECODE": "1", **env})


class FreezeGuardTest(FixCycleFixture):
    def edit(self, rel: str, tool: str = "Edit", **env: str) -> subprocess.CompletedProcess:
        return hook(FREEZE, {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(self.ws),
                             "tool_input": {"file_path": rel}}, **env)

    def shell(self, command: str) -> subprocess.CompletedProcess:
        return hook(KILL, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(self.ws),
                           "tool_input": {"command": command}})

    def test_writes_to_output_are_frozen_after_the_first_receipt_until_a_cycle_opens(self) -> None:
        self.log(0.95)
        self.gate()
        self.assertEqual(self.edit("output/demo/SKILL.md").returncode, 0, "before the panel the skill is editable")
        self.vetoed_receipts_only()
        out = self.edit("output/demo/SKILL.md")
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("freeze guard", out.stderr)
        self.assertIn("--fix-cycle --finding verifier-b.veto", out.stderr, "the command for the finding citing it")
        self.assertEqual(self.edit(str(self.ws / "output" / "demo" / "SKILL.md"), "Write").returncode, 2)
        self.assertEqual(self.edit("work/experiments/craft-decisions.md", "Write").returncode, 0, "work/ writes")
        self.assertEqual(self.edit("input/notes.md", "Write").returncode, 0, "input/ writes")
        self.assertEqual(self.edit("output/demo/SKILL.md", CSA_EXECUTOR="1").returncode, 0, "executors are exempt")
        for command in ("echo x >> output/demo/SKILL.md", "cd output/demo && echo x > SKILL.md",
                        "cp /tmp/a output/demo/notes.md", "rm -f output/demo/SKILL.md", "mv output/demo/a work/",
                        "sed -i '' s/a/b/ output/demo/SKILL.md", "printf x | tee output/demo/x.md"):
            out = self.shell(command)
            self.assertEqual(out.returncode, 2, command)
            self.assertIn("freeze guard", out.stderr, command)
        for command in ("cat output/demo/SKILL.md", "grep -n demo output/demo/SKILL.md 2>/dev/null",
                        "echo x > work/notes.md", "cp output/demo/SKILL.md work/reviewed.md"):
            self.assertEqual(self.shell(command).returncode, 0, command)
        cursor = hook(FREEZE, {"hook_event_name": "preToolUse", "tool_name": "Write", "workspace_roots": [str(self.ws)],
                               "tool_input": {"file_path": "output/demo/SKILL.md"}})
        self.assertEqual(cursor.returncode, 0)
        self.assertEqual(json.loads(cursor.stdout)["permission"], "deny")
        self.run_script("check_gate.py", "--consensus")
        self.open()
        self.assertEqual(self.edit("output/demo/SKILL.md").returncode, 0, "an open fix cycle may edit")
        self.assertEqual(self.shell("echo x >> output/demo/SKILL.md").returncode, 0)

    def vetoed_receipts_only(self) -> None:
        """The full panel's receipts, verifier-b citing a safety veto, with no consensus written yet."""
        for role, pair in HIGH.items():
            extra = {"veto": {"kind": "safety", "evidence": '"name: demo"', "repro": "run it on gold-01"}} \
                if role == "verifier-b" else {}
            self.score(role, pair, **extra)

    def test_no_cycle_left_says_edit_nothing(self) -> None:
        self.log(0.95)
        self.gate()
        self.panel(HIGH)
        out = self.edit("output/demo/SKILL.md")
        self.assertEqual(out.returncode, 2)
        self.assertIn("No fix cycle can open now", out.stderr)
        self.assertIn("--next", out.stderr)

    def test_the_panel_path_phase_4_return_is_not_frozen(self) -> None:
        self.rubric("llm-judge")
        self.log(0.95)
        self.gate()
        for role in HIGH:
            self.score(role, (5, 5), [SCENARIO])
        self.run_script("check_gate.py", "--consensus")
        self.assertIn("LOOP OPEN", self.next())
        self.assertEqual(self.edit("output/demo/SKILL.md").returncode, 0, "ITERATE sends the build back to Phase 4")

    def test_outside_a_build_nothing_is_frozen(self) -> None:
        out = hook(FREEZE, {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(self.root),
                            "tool_input": {"file_path": str(self.root / "output" / "x" / "a.md")}})
        self.assertEqual(out.returncode, 0)
        self.assertEqual(hook(FREEZE, {"garbage": True}).returncode, 0)


class FreezeConfigTest(unittest.TestCase):
    def test_every_host_config_runs_it(self) -> None:
        claude = json.loads((SKILL / "hooks" / "hooks.json").read_text())
        matchers = {m["matcher"]: m["hooks"][0]["command"] for m in claude["hooks"]["PreToolUse"]}
        self.assertIn("freeze-guard", matchers["Edit|Write|MultiEdit|NotebookEdit"])
        cursor = json.loads((SKILL / "hooks" / "cursor-hooks.json").read_text())
        self.assertTrue(any("freeze-guard" in h["command"] for h in cursor["hooks"]["preToolUse"]))
        self.assertIn("freeze_guard.py", (SKILL / "hooks" / "opencode-kill-guard.js").read_text())
        self.assertTrue(os.access(SKILL / "hooks" / "freeze-guard", os.X_OK))

    def test_the_launcher_runs_the_guard(self) -> None:
        out = subprocess.run(["sh", str(SKILL / "hooks" / "freeze-guard")], input="{}", capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        cursor = subprocess.run(["sh", str(SKILL / "hooks" / "freeze-guard")], input=json.dumps(
            {"hook_event_name": "preToolUse", "tool_input": {"file_path": "/nowhere/a.md"}}), capture_output=True,
            text=True, timeout=30)
        self.assertEqual(json.loads(cursor.stdout), {"permission": "allow"})


if __name__ == "__main__":
    unittest.main()
