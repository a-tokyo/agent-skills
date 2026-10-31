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
        """Experiment lines get `held_out` = `metric` unless they set it (None: not measured)."""
        path = self.ws / "work" / "experiments" / "autoresearch.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = tuple({"held_out": e["metric"], **e} if "metric" in e else e for e in entries)
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
        zero = {"experiment": 0, "commit": commit, "metric": 0.9, "held_out": None, "status": "baseline"}
        self.log(zero)
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "the fast path needs the held-out score first")
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
        self.assertEqual(self.loop(), "LOOP DONE: the best held_out is 1.000, its maximum")
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
        self.assertEqual(self.loop(), "LOOP DONE: experiment 2 held out 0.865, no gain above the noise threshold 0.010 "
                                      "over the best 0.860, at target_score 0.85")
        self.log(zero, {"experiment": 1, "commit": commit, "metric": 0.81, "status": "discard"})
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "below target is not this rule")

    def test_a_plateau_below_target_closes_the_loop(self) -> None:
        """Round 6 adversary: target 0.85, plateau_window 2, measured noise 0.03, 0.700 -> 0.705 -> 0.704."""
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        zero = {"experiment": 0, "commit": commit, "metric": 0.7, "status": "baseline"}
        noise = {"type": "noise_estimate", "noise_threshold": 0.03}
        one = {"experiment": 1, "commit": commit, "metric": 0.705, "status": "discard"}
        two = {"experiment": 2, "commit": commit, "metric": 0.704, "status": "discard"}
        self.log(zero, noise, one)
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "one experiment inside the noise is not a plateau of 2")
        self.log(zero, noise, one, two)
        self.assertEqual(self.loop(), "LOOP DONE: a plateau: the last 2 experiments each held out within the noise "
                                      "threshold 0.030 of the best kept before it (plateau_window 2; best 0.700)")
        self.assertTrue(self.denied())
        wider = ({**one, "metric": 0.72}, {**two, "metric": 0.719})  # within 0.03, not within 0.01
        self.log(zero, noise, *wider)
        self.assertTrue(self.loop().startswith("LOOP DONE: a plateau"))
        self.log(zero, *wider)
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "with no estimate the 0.01 floor applies")
        self.log(zero, {"type": "noise_estimate", "noise_threshold": 0.001}, one, two)
        self.assertTrue(self.loop().startswith("LOOP DONE: a plateau"), "an estimate below the floor is raised to it")
        self.log(zero, noise, {**one, "metric": 0.75, "status": "keep"}, {**two, "metric": 0.8, "status": "keep"})
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "gains above the noise are no plateau")
        self.log(zero, noise, one, two, {**two, "experiment": 3, "metric": 0.74, "status": "keep"})
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "a gain resets the run")

    def test_the_plateau_window_comes_from_the_rubric(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        rubric = self.ws / "work" / "evaluation" / "rubric.yaml"
        rubric.write_text(rubric.read_text() + "plateau_window: 3\n")
        entries = [{"experiment": 0, "commit": commit, "metric": 0.7, "status": "baseline"},
                   {"type": "noise_estimate", "noise_threshold": 0.02}]
        entries += [{"experiment": i, "commit": commit, "metric": 0.7 + i / 1000, "status": "discard"} for i in (1, 2)]
        self.log(*entries)
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "two of three")
        self.log(*entries, {"experiment": 3, "commit": commit, "metric": 0.71, "status": "discard"})
        self.assertIn("a plateau: the last 3 experiments", self.loop())
        rubric.write_text(rubric.read_text().replace("plateau_window: 3", "plateau_window: two"))
        self.assertIn("plateau_window 'two' is not a whole number", self.loop())

    def test_above_target_a_plateau_and_the_target_rule_both_close_it(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        zero = {"experiment": 0, "commit": commit, "metric": 0.8, "status": "baseline"}
        noise = {"type": "noise_estimate", "noise_threshold": 0.03}
        one = {"experiment": 1, "commit": commit, "metric": 0.9, "status": "keep"}
        self.log(zero, noise, one)
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "a gain to target keeps it open")
        self.log(zero, noise, one, {"experiment": 2, "commit": commit, "metric": 0.925, "status": "keep"})
        self.assertEqual(self.loop(), "LOOP DONE: experiment 2 held out 0.925, no gain above the noise threshold 0.030 "
                                      "over the best 0.900, at target_score 0.85")

    def test_a_training_ceiling_over_a_lower_held_out_score_keeps_it_open(self) -> None:
        """c18: Haiku builds read 1.0 on their own metric while the hidden holdout scored them 0.85-0.91."""
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        zero = {"experiment": 0, "commit": commit, "metric": 1.0, "held_out": 0.85, "status": "baseline"}
        self.log(zero)
        self.assertEqual(self.loop(), "LOOP OPEN: overfitting signal: experiment 0 overall_score 1.000 is more than "
                                      "the noise threshold 0.010 above its held_out 0.850 (phase-4.md 4.2); "
                                      "generalize or revert, up to max_iterations 20")
        self.assertFalse(self.denied())
        flat = [{"experiment": i, "commit": commit, "metric": 1.0, "held_out": 0.85, "status": "keep"} for i in (1, 2)]
        self.log(zero, *flat)
        self.assertIn("LOOP OPEN: overfitting signal: experiment 2", self.loop(), "the gap outlasts a plateau")
        rubric = self.ws / "work" / "evaluation" / "rubric.yaml"
        rubric.write_text(rubric.read_text().replace("max_iterations: 20", "max_iterations: 2"))
        self.assertEqual(self.loop(), "LOOP DONE: the budget is spent: 2 experiments, max_iterations 2")
        self.assertTrue(self.denied())
        rubric.write_text(rubric.read_text().replace("max_iterations: 2", "max_iterations: lots"))
        self.assertIn("max_iterations 'lots' is not a whole number", self.loop())

    def test_a_plateau_on_the_held_out_score_ends_it_while_training_still_climbs(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        noise = {"type": "noise_estimate", "noise_threshold": 0.05}
        runs = [{"experiment": i, "commit": commit, "metric": m, "held_out": h, "status": "keep"}
                for i, (m, h) in enumerate(((0.70, 0.70), (0.74, 0.72), (0.76, 0.73)))]
        self.log(noise, *runs)
        self.assertEqual(self.loop(), "LOOP DONE: a plateau: the last 2 experiments each held out within the noise "
                                      "threshold 0.050 of the best kept before it (plateau_window 2; best 0.730)")
        self.log(noise, *runs[:2], {**runs[2], "held_out": 0.80})
        self.assertTrue(self.loop().startswith("LOOP OPEN:"), "a held-out gain above the noise is no plateau")

    def test_a_missing_held_out_score_is_not_done(self) -> None:
        commit = self.git("rev-parse", "HEAD")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Edit\n")
        zero = {"experiment": 0, "commit": commit, "metric": 1.0, "status": "baseline"}
        self.log({**zero, "held_out": None})
        self.assertEqual(self.loop(), "LOOP OPEN: experiment 0 logs no numeric held_out: not measured, so not done "
                                      "(phase-4.md 4.2)")
        self.assertFalse(self.denied())
        self.log(zero, {"experiment": 1, "commit": commit, "metric": 1.0, "held_out": "n/a", "status": "discard"})
        self.assertIn("LOOP OPEN: experiment 1 logs no numeric held_out", self.loop(), "a discarded one counts too")
        self.log(zero)
        self.assertTrue(self.denied(), "measured, the 1.0 ceiling holds")

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
