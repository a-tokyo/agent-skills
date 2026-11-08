"""Offline tests for `check_gate.py --next`: the one status command a builder runs at each phase boundary. Over the
usual build states it prints the single next action as the exact command(s), never the gate while the loop is
open or a metric-first skill is unscored, never a restart of a running gate, and never a gate re-run that could
only precede UNSHIPPED.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import unittest

from test_metric_first import MetricFirstFixture
from test_rescore import EVALUATE

HIGH = {"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (9, 9)}


class NextTest(MetricFirstFixture):
    def next(self, **env: str) -> str:
        out = self.run_script("check_gate.py", "--next", **env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout

    def evaluate(self) -> None:
        path = self.ws / "work" / "evaluation" / "evaluate.sh"
        path.write_text(EVALUATE)
        path.chmod(0o755)

    def edit_skill(self, body: str = "# Demo, edited\n") -> None:
        (self.ws / "output" / "demo" / "SKILL.md").write_text(f"---\nname: demo\ndescription: Demo.\n---\n{body}")

    def assert_no_gate(self, out: str) -> None:
        self.assertNotRegex(out, r"(?<!check_)gate\.py", "--next sent the build to the gate:\n" + out)

    def test_phase_3_states_come_first(self) -> None:
        self.assertIn("NEXT: Phase 3.4", self.next())
        self.evaluate()
        out = self.next()
        self.assertIn("NEXT: Phase 3.5", out)
        self.assert_no_gate(out)

    def test_an_open_loop_never_goes_to_the_gate(self) -> None:
        self.evaluate()
        self.log(0.70)
        out = self.next()
        self.assertTrue(out.startswith("NEXT: Phase 4 (phase-4.md): LOOP OPEN"), out)
        self.assertIn("--next", out)
        self.assert_no_gate(out)

    def test_the_fast_path_goes_straight_to_one_exact_gate_command(self) -> None:
        self.evaluate()
        self.log(0.95)
        out = self.next()
        self.assertTrue(out.startswith("NEXT: 5.0: run the gate (phase-5.md 5.0):"), out)
        command = out.splitlines()[1].strip()
        self.assertIn("gate.py --workspace", command)
        self.assertIn("--detach --wait --model '<model the skill targets>'", command)
        self.assertNotIn("--executor", command, "inside Claude Code a model-invoked skill runs plugin mode")
        self.assertNotIn("--runs", command, "never a 1-run smoke gate")
        self.assertNotIn("--wait-timeout", command, "the default 270 s wait fits one 600000 ms shell call")
        self.assertIn("timeout 600000", out, "the shell call's timeout is stated with the command")
        self.assertIn("--executor <single-call|agentic>", self.next(CLAUDECODE=""), "manual mode names an executor")

    def test_unscored_edits_go_to_rescore_not_the_gate(self) -> None:
        self.evaluate()
        # experiment 1 at target with no gain: the loop is done
        self.log(0.95, {"experiment": 1, "commit": self.commit, "metric": 0.95, "held_out": 0.95, "status": "keep"})
        self.edit_skill()
        out = self.next()
        self.assertTrue(out.startswith("NEXT: score output/demo/ as it is now before the gate"), out)
        self.assertIn("rescore.py --workspace", out)
        self.assert_no_gate(out)
        self.git("add", "output")
        self.git("commit", "-q", "-m", "edit")
        split = self.ws / "work" / "evaluation" / "data-split.yaml"
        split.write_text(split.read_text() + 'validation: ["input/v1.md"]\n')
        self.assertIn("RESCORED", self.run_script("rescore.py").stdout)
        self.assertNotIn("rescore.py", self.next(), "once scored, --next moves on (here: the rescore's overfit)")

    def test_happy_path_needs_only_next_and_the_commands_it_prints(self) -> None:
        self.evaluate()
        self.log(0.95)
        self.gate()
        out = self.next()
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("GATE VALID:"), out)
        self.assertIn("NEXT: 5.1 premortem to work/experiments/premortem.md, then 5.2 panel round 1", lines[1])
        self.assertIn("--panel-receipt <role> <scores.json>", out)
        (self.ws / "work" / "experiments" / "premortem.md").write_text("# Risks\n")
        self.assertNotIn("premortem", self.next())
        self.assertEqual(self.submit("verifier-a", (9, 9)).returncode, 0)
        out = self.next()
        self.assertIn("NEXT: 5.2 panel round 1: still to score: verifier-b, devils-advocate", out)
        for role in ("verifier-b", "devils-advocate"):
            self.assertEqual(self.submit(role, (9, 9)).returncode, 0)
        out = self.next()  # writes the consensus itself: no separate --consensus call
        self.assertTrue((self.ws / "work" / "experiments" / "panel" / "consensus.json").is_file())
        self.assertIn("NEXT: 5.4: write BENCHMARK.md at the build root with `Verdict: SHIP`", out)
        verbatim = [line for line in out.splitlines() if line.startswith(("GATE VALID:", "PANEL CONSENSUS:", "SHIP RULE:"))]
        self.assertEqual(len(verbatim), 3, out)
        self.assertIn("edit nothing in output/ now", out)
        (self.ws / "BENCHMARK.md").write_text("Verdict: SHIP\n\n" + "\n".join(verbatim) + "\n")
        self.assertEqual(self.next().strip(), "BUILD FINISHED: verdict SHIP; follow references/publishing.md")

    def test_a_skill_edited_after_the_review_ends_unshipped_without_a_gate(self) -> None:
        self.evaluate()
        self.log(0.95)
        self.gate()
        consensus = self.panel(HIGH)[0]
        self.edit_skill()
        out = self.next()
        self.assertIn("`Verdict: UNSHIPPED` (the panel ran its 1 round(s) and the skill changed since", out)
        self.assertIn(consensus, out)
        self.assert_no_gate(out)
        (self.ws / "BENCHMARK.md").write_text(f"Verdict: UNSHIPPED\n\n{consensus}\n")
        self.assertEqual(self.next().strip(), "BUILD FINISHED: verdict UNSHIPPED")

    def test_a_running_gate_is_waited_on_never_restarted(self) -> None:
        self.evaluate()
        self.log(0.95)
        out = self.run_script("gate.py", "--detach", "--model", "m", FAKE_MODE="hang")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        try:
            out = self.next()
            self.assertIn("NEXT: 5.0: the gate is running; wait on it", out)
            self.assertIn("gate.py --workspace", out)
            self.assertIn(" --wait", out)
            self.assertNotIn("--restart", out)
        finally:
            self.run_script("gate.py", "--stop")

    def test_an_unusable_gate_reruns_with_its_own_options(self) -> None:
        self.evaluate()
        self.log(0.95)
        out = self.run_script("gate.py", "--model", "m", "--jobs", "4", "--allow-tools", "Bash", "Write", "Read",
                              FAKE_MODE="fewruns")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        out = self.next()
        self.assertIn("NEXT: 5.0: the last gate is not usable", out)
        command = out.splitlines()[1]
        self.assertIn("--restart --wait --model m", command, "--detach --wait would report the same result again")
        self.assertIn("--allow-tools Bash Write Read", command)
        self.assertIn("--jobs 4", command)

    def test_a_smoke_gate_is_followed_by_the_full_gate_never_another_smoke(self) -> None:
        self.evaluate()
        self.log(0.95)
        self.assertEqual(self.run_script("gate.py", "--model", "m", "--runs", "1").returncode, 1)
        command = self.next().splitlines()[1]
        self.assertIn("--restart --wait --model m", command)
        self.assertNotIn("--runs", command, "--next drops a --runs below the required count")

    def test_an_edit_after_the_gate_goes_through_phase_4_not_straight_back_to_the_gate(self) -> None:
        self.rubric("llm-judge")
        self.evaluate()
        self.log(0.95)
        self.gate()
        self.edit_skill()
        out = self.next()
        self.assertTrue(out.startswith("NEXT: Phase 4 (phase-4.md): LOOP OPEN"), out)
        self.assert_no_gate(out)

    def test_a_trigger_miss_returns_to_the_description_pass(self) -> None:
        self.evaluate()
        self.log(0.95)
        self.run_script("gate.py", "--model", "m", FAKE_TRIGGER_MISSES="1")
        out = self.next()
        self.assertTrue(out.startswith("NEXT: 4.6: case 'trigger-1': the skill did not fire"), out)

    def test_synthesis_is_the_next_action(self) -> None:
        self.evaluate()
        self.log(0.95)
        self.gate()
        for role, pair in {"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (2, 2)}.items():
            self.assertEqual(self.submit(role, pair).returncode, 0)
        out = self.next()
        self.assertTrue(out.startswith("NEXT: 5.3: SYNTHESIS NEEDED on"), out)
        self.assert_no_gate(out)

    def test_panel_path_iterate_returns_to_phase_4_then_ends_when_the_budget_is_spent(self) -> None:
        self.rubric("llm-judge")
        self.evaluate()
        self.log(0.95)
        self.gate()
        self.panel({"verifier-a": (5, 5), "verifier-b": (5, 6), "devils-advocate": (5, 5)})
        out = self.next()
        self.assertIn("NEXT: Phase 4 (phase-4.md): LOOP OPEN: the panel's latest consensus, ITERATE", out)
        self.assertIn("fold every panel finding into this one experiment", out)
        rubric = self.ws / "work" / "evaluation" / "rubric.yaml"
        rubric.write_text(rubric.read_text().replace("max_iterations: 20", "max_iterations: 1"))
        log = self.ws / "work" / "experiments" / "autoresearch.jsonl"
        lines = log.read_text()
        self.log(0.95, {"experiment": 1, "commit": self.commit, "metric": 0.9, "held_out": 0.9, "status": "discard"})
        consensus = self.ws / "work" / "experiments" / "panel" / "consensus.json"
        consensus.touch()  # newer than the log again: a second ITERATE with the budget spent
        out = self.next()
        self.assertIn("`Verdict: UNSHIPPED` (a terminal ITERATE: no Phase 4 budget left)", out)
        self.assert_no_gate(out)
        self.assertTrue(lines)

    def test_json(self) -> None:
        data = json.loads(self.run_script("check_gate.py", "--next", "--json").stdout)
        self.assertEqual(data["state"], "evaluate")


if __name__ == "__main__":
    unittest.main()
