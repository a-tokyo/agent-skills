"""Offline tests for scripts/rescore.py: one command scores the skill as it is now and logs the experiment line the
metric-first ship rule reads (phase-4.md 4.2, SKILL.md 5.4); check_gate.py prints that command wherever it finds
the metric missing for the shipped skill.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SCRIPTS
from test_metric_first import MetricFirstFixture

HIGH = {"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (9, 9)}
# A metric that counts its runs: training 0.92 with a dimension, each held-out case 0.88; ./judge-down fails it.
EVALUATE = """#!/bin/bash
[ -f judge-down ] && { echo judge down >&2; exit 3; }
echo run >> runs.log
if [ $# -eq 0 ]; then echo "METRIC correctness=0.9000"; echo "METRIC overall_score=0.9200"
else echo "case $1"; echo "METRIC overall_score=0.8800"; fi
"""


class RescoreTest(MetricFirstFixture):
    def setUp(self) -> None:
        super().setUp()
        evaluation = self.ws / "work" / "evaluation"
        split = evaluation / "data-split.yaml"
        split.write_text(split.read_text() + 'validation: ["input/v1.md", "input/v2.md"]\n')
        (evaluation / "evaluate.sh").write_text(EVALUATE)
        self.log(0.91)
        self.edit_skill("Fixed after the panel.")

    def edit_skill(self, body: str, commit: bool = True) -> None:
        (self.ws / "output" / "demo" / "SKILL.md").write_text(f"---\nname: demo\ndescription: Demo.\n---\n# Demo\n\n{body}\n")
        if commit:
            self.git("add", "output")
            self.git("commit", "-q", "-m", "post-loop fix")

    def runs(self) -> int:
        path = self.ws / "runs.log"
        return len(path.read_text().splitlines()) if path.is_file() else 0

    def lines(self) -> list[dict]:
        path = self.ws / "work" / "experiments" / "autoresearch.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

    def command(self) -> str:
        return f"python3 {SCRIPTS / 'rescore.py'} --workspace {self.ws.resolve()}"

    def test_it_logs_the_current_commit_and_the_ship_rule_then_reads_it(self) -> None:
        self.gate()
        consensus, rule, hint = self.panel(HIGH)
        self.assertIn("metric: none (no kept experiment", rule)
        self.assertTrue(rule.startswith("SHIP RULE: UNSHIPPED"), rule)
        self.assertEqual(hint, self.command(), "the command on its own line, ready to run")
        out = self.run_script("rescore.py")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("RESCORED: experiment 1 commit", out.stdout)
        line = self.lines()[-1]
        self.assertEqual((line["experiment"], line["commit"], line["metric"], line["held_out"], line["status"]),
                         (1, self.git("rev-parse", "HEAD"), 0.92, 0.88, "rescore"))
        self.assertEqual(line["scores"], {"correctness": 0.9})
        self.assertNotIn("type", line, "check_gate.py reads only experiment lines; a typed line is invisible")
        self.assertEqual(self.runs(), 3, "the training metric once, each of the two validation cases once")
        out = self.run_script("check_gate.py", "--consensus")
        self.assertEqual(out.returncode, 0, out.stdout)
        consensus, rule = out.stdout.strip().splitlines()
        self.assertEqual(rule, "SHIP RULE: SHIP (metric-first); gate: every gold delta > 0; metric: experiment 1 "
                               "0.920, held_out 0.880 -> 0.880 target 0.85; veto: none")
        self.assertTrue(self.finish("SHIP", consensus, rule)["finished"])

    def test_a_second_run_changes_nothing(self) -> None:
        self.assertEqual(self.run_script("rescore.py").returncode, 0)
        before, runs = self.lines(), self.runs()
        out = self.run_script("rescore.py")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertTrue(out.stdout.startswith("ALREADY SCORED: experiment 1 0.920, held_out 0.880"), out.stdout)
        self.assertEqual((self.lines(), self.runs()), (before, runs))

    def test_leave_one_out_scores_the_case_missing_from_training(self) -> None:
        split = self.ws / "work" / "evaluation" / "data-split.yaml"
        text = split.read_text().replace('validation: ["input/v1.md", "input/v2.md"]\n', "")
        split.write_text(text.replace("training: []", 'all_cases: ["input/a.md", "input/b.md"]\n'
                                                      'training: ["input/a.md"]'))
        out = self.run_script("rescore.py")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(self.lines()[-1]["held_out_cases"], ["input/b.md"])
        self.assertEqual(self.runs(), 2)

    def test_a_missing_evaluate_script_is_a_clear_error(self) -> None:
        (self.ws / "work" / "evaluation" / "evaluate.sh").unlink()
        out = self.run_script("rescore.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("CANNOT RESCORE: no work/evaluation/evaluate.sh", out.stdout)
        self.assertEqual(len(self.lines()), 1, "nothing logged")

    def test_uncommitted_skill_changes_print_the_commit_to_run(self) -> None:
        self.edit_skill("Edited, not committed.", commit=False)
        out = self.run_script("rescore.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("output/demo/ has uncommitted changes", out.stdout)
        self.assertIn(f'commit -m "rescore demo" -- output/demo', out.stdout)
        self.assertEqual(self.runs(), 0)

    def test_a_failing_metric_logs_nothing(self) -> None:
        (self.ws / "work" / "evaluation" / "evaluate.sh").write_text("#!/bin/bash\necho judge down >&2\nexit 3\n")
        out = self.run_script("rescore.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("exited 3 without a `METRIC overall_score=<x>` line", out.stdout)
        self.assertIn("judge down", out.stdout)
        self.assertEqual(len(self.lines()), 1)

    def test_check_gate_prints_the_command_wherever_the_metric_is_missing(self) -> None:
        self.gate()
        consensus, rule, hint = self.panel(HIGH)
        data = self.finish("SHIP", consensus, rule)
        self.assertIn("the Phase 4 metric is unset for output/demo/ as it is now", data["reason"])
        self.assertEqual(data["reason"].splitlines()[-1], self.command())
        finish = self.run_script("check_gate.py", "--finish").stdout.splitlines()
        self.assertEqual(finish[-1], self.command(), "the --finish line ends with the command on its own line")

    def test_unshipped_only_for_want_of_a_score_is_sent_back_to_score_it(self) -> None:
        self.gate()
        consensus, rule, _ = self.panel(HIGH)
        data = self.finish("UNSHIPPED", consensus, rule)
        self.assertFalse(data["finished"], "the d18 Haiku shape: UNSHIPPED because the fix was never scored")
        self.assertIn("the Phase 4 metric is unset for output/demo/", data["reason"])
        self.assertEqual(data["reason"].splitlines()[-1], self.command())

    def test_unshipped_stands_when_the_rescore_cannot_score(self) -> None:
        self.gate()
        consensus, rule, _ = self.panel(HIGH)
        (self.ws / "judge-down").touch()  # not a gate input: the gate stays valid
        self.assertFalse(self.finish("UNSHIPPED", consensus, rule)["finished"], "not tried yet")
        self.assertEqual(self.run_script("rescore.py").returncode, 1)
        self.assertTrue(self.finish("UNSHIPPED", consensus, rule)["finished"], "tried, and the metric cannot run")
        self.assertIn("UNSHIPPED stands", self.finish("SHIP", consensus, rule)["reason"])

    def test_an_uncommitted_skill_does_not_count_as_a_failed_rescore(self) -> None:
        self.gate()
        consensus, rule, _ = self.panel(HIGH)
        self.edit_skill("Edited, not committed.", commit=False)
        self.assertEqual(self.run_script("rescore.py").returncode, 1)
        attempts = self.ws / "work" / "evaluation" / "gate-attempts.jsonl"
        self.assertNotIn('"rescore"', attempts.read_text() if attempts.is_file() else "")

    def test_a_veto_keeps_unshipped_without_a_score(self) -> None:
        self.gate()
        veto = {"kind": "safety", "evidence": '"name: demo"', "repro": "run it on input/gold-01.md: it deletes the workspace"}
        consensus, rule, hint = self.panel(HIGH, **{"devils-advocate": {"veto": veto}})
        self.assertIn("veto: devils-advocate", rule)
        self.assertEqual(hint, self.command())
        self.assertTrue(self.finish("UNSHIPPED", consensus, rule)["finished"])

    def test_loop_done_prints_the_command_when_the_skill_is_unscored(self) -> None:
        self.log(1.0)  # held out at the 1.0 ceiling: the loop is done, and the fix after it is unscored
        out = self.run_script("check_gate.py", "--loop")
        lines = out.stdout.strip().splitlines()
        self.assertTrue(lines[0].startswith("LOOP DONE:"), out.stdout)
        self.assertIn("has no score", lines[0])
        self.assertEqual(lines[-1], self.command())
        self.assertEqual(self.run_script("rescore.py").returncode, 0)
        out = self.run_script("check_gate.py", "--loop")
        self.assertNotIn("rescore.py", out.stdout, "scored: no command")

    def test_the_finish_guard_send_back_carries_the_command(self) -> None:
        self.gate()
        consensus, rule, _ = self.panel(HIGH)
        self.finish("SHIP", consensus, rule)
        guard = SCRIPTS.parent / "hooks" / "finish_guard.py"
        env = {k: v for k, v in os.environ.items() if k != "CSA_EXECUTOR"}
        out = subprocess.run([sys.executable, "-B", str(guard)], input=json.dumps(
            {"hook_event_name": "Stop", "cwd": str(self.ws), "stop_hook_active": False}),
            capture_output=True, text=True, timeout=60, env=env)
        reason = json.loads(out.stdout)["reason"]
        self.assertIn("\n" + self.command() + "\n", reason)

    def test_the_loop_guard_lets_the_rescore_through_when_the_loop_is_done(self) -> None:
        sys.path.insert(0, str(SCRIPTS.parent / "hooks"))
        import kill_guard  # noqa: E402

        self.assertIsNone(kill_guard.loop_block(self.command(), str(self.ws)))

    def panel(self, scores: dict = HIGH, **extra: dict) -> list[str]:  # type: ignore[override]
        lines = super().panel(scores, **extra)
        return lines + [""] * (3 - len(lines))


class RescoreHelpTest(unittest.TestCase):
    def test_help_names_what_it_does_and_its_exit_codes(self) -> None:
        out = subprocess.run([sys.executable, "-B", str(SCRIPTS / "rescore.py"), "--help"], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0)
        for need in ("ALREADY SCORED", "RESCORED", "held_out", "Exit codes"):
            self.assertIn(need, out.stdout)


if __name__ == "__main__":
    unittest.main()
