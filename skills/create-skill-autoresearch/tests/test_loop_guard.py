"""Offline tests for the loop guard: check_gate.py --loop (when the Phase 4 loop is over) and the kill guard
hook that denies the metric command then (phase-4.md 4.3).

Fixtures (temp workspaces) are in helpers.py. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest

from helpers import SCRIPTS, TempWorkspace

HOOK = SCRIPTS.parent / "hooks" / "kill_guard.py"
METRIC = "./work/evaluation/evaluate.sh > run.log 2>&1"


class LoopGuardTest(TempWorkspace):
    def setUp(self) -> None:
        super().setUp()
        skill = self.ws / "output" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Demo\n")
        evaluation = self.ws / "work" / "evaluation"
        evaluation.mkdir(parents=True)
        (evaluation / "data-split.yaml").write_text('strategy: "leave-one-out"\ntraining: ["input/a.md"]\n')
        (evaluation / "evaluate.sh").write_text("#!/bin/sh\necho METRIC overall_score=1.0\n")
        self.write_rubric(target=0.85)
        self.git("init", "-q")
        self.commit("baseline")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=t", "-c", "user.email=t@t",
                               "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, message: str) -> str:
        self.git("add", "output")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def log(self, *entries: dict) -> None:
        path = self.ws / "work" / "experiments" / "autoresearch.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        self.later(path)

    def later(self, path) -> None:
        """Make `path` strictly newer than every file written before it (mtime order is the guard's clock)."""
        self.clock = getattr(self, "clock", time.time() + 10) + 10
        os.utime(path, (self.clock, self.clock))

    def loop(self) -> str:
        out = self.run_script("check_gate.py", "--loop")
        self.assertEqual(out.returncode, 0 if out.stdout.startswith("LOOP DONE:") else 1, out.stdout + out.stderr)
        return out.stdout.strip()

    def hook(self, command: str = METRIC, cursor: bool = False, **env: str) -> subprocess.CompletedProcess:
        event = ({"hook_event_name": "beforeShellExecution", "command": command, "cwd": str(self.ws)} if cursor else
                 {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.ws)})
        full = {k: v for k, v in os.environ.items() if k != "CSA_EXECUTOR"} | env
        return subprocess.run([sys.executable, "-B", str(HOOK)], input=json.dumps(event), capture_output=True,
                              text=True, timeout=30, env=full)

    def denied(self, **kw: object) -> bool:
        out = self.hook(**kw)
        if out.returncode == 2:
            self.assertIn("Phase 4 is done (", out.stderr)
            self.assertIn("go to 5.0", out.stderr)
            return True
        self.assertEqual(out.returncode, 0, out.stderr)
        return False

    def test_no_experiment_leaves_the_loop_open(self) -> None:
        self.assertEqual(self.loop(), "LOOP OPEN: no scored experiment logged yet")
        self.assertFalse(self.denied())

    def test_the_fast_path_closes_the_loop_once_validated(self) -> None:
        split = self.ws / "work" / "evaluation" / "data-split.yaml"
        split.write_text('strategy: "70-20-10"\ntraining: ["input/a.md"]\nvalidation: ["input/v.md"]\n'
                         'test: ["input/t.md"]\n')
        commit = self.commit("split")
        zero = {"experiment": 0, "commit": commit, "metric": 0.9, "status": "baseline"}
        self.log(zero)
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "the fast path needs the validation check first")
        self.assertFalse(self.denied())
        self.assertFalse(self.denied(command="./work/evaluation/evaluate.sh input/v.md"), "validation scoring runs")
        self.log(zero, {"type": "validation_check", "experiment": 0, "validation_score": 0.9})
        self.assertEqual(self.loop(), "LOOP DONE: experiment 0 meets target_score: the 3.5 fast path, so run 3.6 "
                                      "first")
        self.assertTrue(self.denied())
        self.assertFalse(self.denied(command="./work/evaluation/evaluate.sh input/v.md"), "a case argument runs")

    def test_the_ceiling_closes_the_loop(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        self.log({"experiment": 0, "commit": commit, "metric": 1.0, "status": "baseline"})
        self.assertEqual(self.loop(), "LOOP DONE: the best overall_score is 1.000, its maximum")
        for command in (METRIC, "cd work/evaluation && ./evaluate.sh", f"bash {self.ws}/work/evaluation/evaluate.sh",
                        "M=$(work/evaluation/evaluate.sh | tail -1)"):
            with self.subTest(command=command):
                self.assertTrue(self.denied(command=command))
        for command in ("cat work/evaluation/evaluate.sh", "chmod +x work/evaluation/evaluate.sh",
                        "work/evaluation/evaluate.sh input/a.md", "ls"):
            with self.subTest(command=command):
                self.assertFalse(self.denied(command=command))

    def test_at_target_with_no_gain_closes_the_loop(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        zero = {"experiment": 0, "commit": commit, "metric": 0.8, "status": "baseline"}
        self.log(zero, {"experiment": 1, "commit": commit, "metric": 0.86, "status": "keep"})
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "a gain to target keeps it open")
        self.log(zero, {"experiment": 1, "commit": commit, "metric": 0.86, "status": "keep"},
                 {"experiment": 2, "commit": commit, "metric": 0.865, "status": "keep"})
        self.assertEqual(self.loop(), "LOOP DONE: experiment 2 scored 0.865, no gain above the 0.01 noise floor over "
                                      "the best 0.860, at target_score 0.85")
        self.log(zero, {"experiment": 1, "commit": commit, "metric": 0.81, "status": "discard"})
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "below target is not this rule")

    def test_an_iterate_consensus_reopens_it_for_one_experiment(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        zero = {"experiment": 0, "commit": commit, "metric": 1.0, "status": "baseline"}
        self.log(zero)
        consensus = self.ws / "work" / "experiments" / "panel" / "consensus.json"
        consensus.parent.mkdir(parents=True)
        consensus.write_text(json.dumps({"label": "SHIP WITH CAVEATS"}))
        self.later(consensus)
        self.assertTrue(self.denied(), "a consensus that ships does not reopen it")
        consensus.write_text(json.dumps({"label": "ITERATE"}))
        self.later(consensus)
        self.assertEqual(self.loop(), "LOOP OPEN: the panel's latest consensus, ITERATE, is newer than the last "
                                      "logged experiment")
        self.assertFalse(self.denied())
        self.log(zero, {"experiment": 1, "commit": commit, "metric": 1.0, "status": "keep"})
        self.assertTrue(self.denied(), "the experiment after the consensus closes it again")
        self.log(zero, {"experiment": 1, "commit": commit, "metric": 1.0, "status": "keep"},
                 {"experiment": 2, "commit": commit, "metric": 1.0, "status": "discard"})
        self.assertTrue(self.denied(), "so does a reverted one")

    def test_a_gold_delta_at_or_below_zero_reopens_a_fast_path_build(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        self.log({"experiment": 0, "commit": commit, "metric": 0.9, "status": "baseline"})
        self.assertTrue(self.denied())
        evaluation = self.ws / "work" / "evaluation"
        with (evaluation / "data-split.yaml").open("a") as fh:
            fh.write('plugin_eval:\n  - "input/a.md"\n  - "input/b.md"\n')
        (evaluation / "uplift.json").write_text(json.dumps({"partial": False, "cases": [
            {"case": "input/a.md", "with": [1.0] * 3, "without": [0.0] * 3},
            {"case": "input/b.md", "with": [0.5] * 3, "without": [0.5] * 3}]}))
        (evaluation / "gate.json").write_text(json.dumps({"mode": "manual", "skill": "demo",
                                                          "finished": "2026-10-26T00:00:00+00:00"}))
        self.later(evaluation / "gate.json")
        self.assertEqual(self.loop(), "LOOP OPEN: the gate scored gold delta<=0 on input/b.md with only experiment 0 "
                                      "logged (phase-3.md 3.5)")
        self.assertFalse(self.denied())

    def test_a_description_only_change_may_be_scored(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        self.log({"experiment": 0, "commit": commit, "metric": 1.0, "status": "baseline"})
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text("---\nname: demo\ndescription: >\n  Sharper.\n  Two lines.\n---\n# Demo\n")
        self.assertEqual(self.loop(), "LOOP OPEN: only the description changed since experiment 0: the 4.6 pass may "
                                      "score it")
        skill.write_text("---\nname: demo\ndescription: Sharper.\n---\n# Demo, edited\n")
        self.assertTrue(self.loop().startswith("LOOP DONE:"), "a body edit is an experiment")

    def test_executor_sessions_cursor_and_other_directories(self) -> None:
        self.log({"experiment": 0, "commit": self.git("rev-parse", "HEAD"), "metric": 1.0, "status": "baseline"})
        self.assertTrue(self.denied())
        self.assertFalse(self.denied(CSA_EXECUTOR="1"))
        out = self.hook(cursor=True)
        reply = json.loads(out.stdout)
        self.assertEqual((out.returncode, reply["permission"]), (0, "deny"))
        self.assertIn("Phase 4 is done", reply["agent_message"])
        self.assertEqual(json.loads(self.hook(command="ls", cursor=True).stdout), {"permission": "allow"})
        other = self.root / "elsewhere"
        (other / "work" / "evaluation").mkdir(parents=True)
        out = self.hook(command=f"cd {other} && ./work/evaluation/evaluate.sh")
        self.assertEqual(out.returncode, 0, "not a build: allowed")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_opencode_plugin_resolves_a_relative_metric_against_its_directory(self) -> None:
        """OpenCode sends no cwd of its own: the plugin passes its `directory` init argument as the event's cwd."""
        self.log({"experiment": 0, "commit": self.git("rev-parse", "HEAD"), "metric": 1.0, "status": "baseline"})
        plugin = (HOOK.parent / "opencode-kill-guard.js").as_uri()

        def call(directory: str) -> str:
            script = f"""
                import {{ KillGuard }} from {json.dumps(plugin)};
                const hooks = await KillGuard({{ directory: {json.dumps(directory)} }});
                try {{
                  await hooks["tool.execute.before"]({{ tool: "bash" }}, {{ args: {{ command: {json.dumps(METRIC)} }} }});
                  console.log("allowed");
                }} catch (error) {{ console.log("blocked: " + error.message); }}
            """
            env = {k: v for k, v in os.environ.items() if k != "CSA_EXECUTOR"} | {"PYTHONDONTWRITEBYTECODE": "1"}
            out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True,
                                 text=True, timeout=60, env=env, cwd=self.root)
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.strip()

        self.assertIn("Phase 4 is done", call(str(self.ws)))
        self.assertEqual(call(str(self.root)), "allowed", "the same relative path from another directory")


if __name__ == "__main__":
    unittest.main()
