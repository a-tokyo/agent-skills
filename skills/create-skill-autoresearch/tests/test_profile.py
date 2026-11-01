"""Offline tests for the lite profile (verdict-rules.json `lite`): small builder models run the build on fewer gate
runs, smaller Phase 4 caps and a one-reviewer panel. The profile lives in work/manifest.yaml (`check_gate.py --next
--profile lite`, once) and CSA_PROFILE forces it; the full profile is unchanged.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from helpers import SCRIPTS, TempWorkspace
import test_metric_first
import test_rescore
import check_gate  # noqa: E402  (on sys.path through helpers)
import gate_common  # noqa: E402

SKILL = SCRIPTS.parent


class ProfileTest(TempWorkspace):
    def manifest(self) -> Path:
        return self.ws / "work" / "manifest.yaml"

    def test_full_unless_the_manifest_or_the_environment_says_lite(self) -> None:
        with mock.patch.dict(os.environ, {"CSA_PROFILE": ""}):
            self.assertEqual(gate_common.profile(self.ws), "full")
            self.manifest().parent.mkdir(parents=True)
            self.manifest().write_text("pairs: []\nprofile: lite  # set once\n")
            self.assertEqual(gate_common.profile(self.ws), "lite")
            self.manifest().write_text("profile: turbo\n")
            self.assertEqual(gate_common.profile(self.ws), "full", "an unknown profile is the full one")
        with mock.patch.dict(os.environ, {"CSA_PROFILE": "lite"}):
            self.assertEqual(gate_common.profile(self.ws), "lite", "the environment forces it")
        self.manifest().write_text("profile: lite\n")
        with mock.patch.dict(os.environ, {"CSA_PROFILE": "full"}):
            self.assertEqual(gate_common.profile(self.ws), "full", "the environment wins over the manifest")

    def test_next_profile_lite_stores_it_once_and_every_later_call_reads_it(self) -> None:
        self.make_build(["gold-01"])
        self.manifest().write_text("pairs:\n  - input: input/a.md\n    reference: input/a.json\n")
        out = self.run_script("check_gate.py", "--next", "--profile", "lite", CSA_PROFILE="")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("PROFILE: lite"), out.stdout)
        text = self.manifest().read_text()
        self.assertIn("pairs:\n  - input: input/a.md\n    reference: input/a.json\n", text, "other lines kept")
        self.assertEqual(text.count("profile: lite"), 1)
        self.assertTrue(self.run_script("check_gate.py", "--next", CSA_PROFILE="").stdout.startswith("PROFILE: lite"))
        self.run_script("check_gate.py", "--next", "--profile", "lite", CSA_PROFILE="")
        self.assertEqual(self.manifest().read_text().count("profile:"), 1, "re-running replaces, never appends")
        self.run_script("check_gate.py", "--next", "--profile", "full", CSA_PROFILE="")
        self.assertNotIn("PROFILE: lite", self.run_script("check_gate.py", "--next", CSA_PROFILE="").stdout)

    def test_profile_goes_with_next(self) -> None:
        self.make_build(["gold-01"])
        out = self.run_script("check_gate.py", "--finish", "--profile", "lite")
        self.assertEqual(out.returncode, 2)
        self.assertIn("--profile goes with --next", out.stderr)


class LiteGateTest(TempWorkspace):
    """The lite gate keeps every validity rule but runs 2 per arm (verdict-rules.json lite runs_per_arm)."""

    def setUp(self) -> None:
        super().setUp()
        self.make_build(["gold-01", "trigger-1"])
        (self.ws / "work" / "manifest.yaml").write_text("profile: lite\n")

    def test_two_runs_per_arm_by_default_and_valid(self) -> None:
        out = self.run_script("gate.py", "--model", "m", CSA_PROFILE="")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("GATE VALID", out.stdout)
        argv = [json.loads(line) for line in self.argv_log.read_text().splitlines()]
        runs = [a[a.index("--runs") + 1] for a in argv if "--runs" in a]
        self.assertEqual(runs, ["2"])
        self.assertEqual((self.evaluation("gate.json")["runs"], self.evaluation("gate.json")["profile"]), (2, "lite"))

    def test_one_run_is_never_enough_and_full_still_needs_three(self) -> None:
        self.assertEqual(self.run_script("gate.py", "--model", "m", "--runs", "1", CSA_PROFILE="").returncode, 1)
        self.assertIn("1 completed runs; 2 required", self.run_script("check_gate.py", CSA_PROFILE="").stdout)
        self.assertEqual(self.run_script("gate.py", "--model", "m", CSA_PROFILE="").returncode, 0)
        out = self.run_script("check_gate.py", CSA_PROFILE="full")
        self.assertIn("2 completed runs; 3 required", out.stdout, "a lite gate is no full-profile gate")

    def test_the_gate_command_carries_no_runs_override(self) -> None:
        self.assertEqual(self.run_script("gate.py", "--model", "m", CSA_PROFILE="").returncode, 0)
        with mock.patch.dict(os.environ, {"CSA_PROFILE": ""}):
            command = check_gate.gate_command(self.ws, "demo")
        self.assertIn("--model m", command)
        self.assertNotIn("--runs", command, "the recorded 2 runs are the lite default, not an override")


class LiteLoopTest(test_metric_first.MetricFirstFixture):
    """Phase 4 caps: the lower of rubric.yaml's max_iterations / plateau_window and lite's (3 / 2)."""

    def experiments(self, *held: float) -> None:
        self.log(held[0], *({"experiment": n, "commit": self.commit, "metric": h, "held_out": h, "status": "keep"}
                            for n, h in enumerate(held[1:], 1)))

    def loop(self, profile: str) -> str:
        return self.run_script("check_gate.py", "--loop", CSA_PROFILE=profile).stdout

    def test_three_experiments_spend_the_lite_budget(self) -> None:
        self.experiments(0.50, 0.60, 0.70, 0.75)  # each gains, under target_score 0.85: no other stop holds
        self.assertTrue(self.loop("full").startswith("LOOP OPEN"), self.loop("full"))
        self.assertIn("LOOP DONE: the budget is spent: 3 experiments, max_iterations 3", self.loop("lite"))

    def test_lite_caps_a_longer_plateau_window_at_two(self) -> None:
        rubric = self.ws / "work" / "evaluation" / "rubric.yaml"
        rubric.write_text(rubric.read_text().replace("max_iterations: 20", "max_iterations: 20\nplateau_window: 5"))
        self.experiments(0.50, 0.50, 0.50)
        self.assertTrue(self.loop("full").startswith("LOOP OPEN"), self.loop("full"))
        self.assertIn("plateau_window 2", self.loop("lite"))

    def test_a_smaller_rubric_value_stands(self) -> None:
        rubric = self.ws / "work" / "evaluation" / "rubric.yaml"
        rubric.write_text(rubric.read_text().replace("max_iterations: 20", "max_iterations: 1"))
        self.experiments(0.50, 0.60)
        self.assertIn("max_iterations 1", self.loop("lite"))


class LiteRescoreTest(test_metric_first.MetricFirstFixture):
    """The lite profile re-scores each skill commit once; a second call is refused with the next step."""

    def setUp(self) -> None:  # test_rescore.RescoreTest's build, without inheriting its tests
        super().setUp()
        evaluation = self.ws / "work" / "evaluation"
        split = evaluation / "data-split.yaml"
        split.write_text(split.read_text() + 'validation: ["input/v1.md", "input/v2.md"]\n')
        (evaluation / "evaluate.sh").write_text(test_rescore.EVALUATE)
        self.log(0.91)
        self.edit_skill("Fixed after the panel.")

    edit_skill = test_rescore.RescoreTest.edit_skill
    runs = test_rescore.RescoreTest.runs

    def test_a_second_rescore_on_the_same_commit_is_refused_with_the_next_step(self) -> None:
        self.assertIn("RESCORED", self.run_script("rescore.py", CSA_PROFILE="lite").stdout)
        out = self.run_script("rescore.py", CSA_PROFILE="lite")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("RESCORE REFUSED: commit", out.stdout)
        self.assertIn("check_gate.py --workspace", out.stdout)
        self.assertIn("--next", out.stdout)
        self.assertEqual(self.runs(), 3, "nothing re-ran")
        self.assertIn("ALREADY SCORED", self.run_script("rescore.py", CSA_PROFILE="full").stdout, "full: unchanged")

    def test_a_failed_rescore_is_not_retried_on_the_same_commit(self) -> None:
        (self.ws / "judge-down").write_text("")
        self.assertIn("CANNOT RESCORE", self.run_script("rescore.py", CSA_PROFILE="lite").stdout)
        (self.ws / "judge-down").unlink()
        self.assertIn("RESCORE REFUSED", self.run_script("rescore.py", CSA_PROFILE="lite").stdout)
        self.edit_skill("Fixed again.")
        self.assertIn("RESCORED", self.run_script("rescore.py", CSA_PROFILE="lite").stdout, "a new commit scores")


class LitePanelTest(TempWorkspace):
    """The lite panel: one `reviewer` (references/panel/reviewer.md), no synthesis round; its receipt is the consensus.
    The full profile accepts only its three roles, the lite one only the reviewer."""

    LOW = (5, 6)  # an ITERATE on the panel path

    def setUp(self) -> None:
        super().setUp()
        self.make_build(["gold-01", "trigger-1"])
        self.write_rubric()
        (self.ws / "work" / "manifest.yaml").write_text("profile: lite\n")

    def script(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_script(*args, CSA_PROFILE="")

    def gate(self) -> None:
        out = self.script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def review(self, scores: tuple[float, float], escalation: str | None = None, veto: dict | None = None,
               role: str = "reviewer", synthesis: bool = False) -> subprocess.CompletedProcess:
        def dim(score: float) -> dict:
            return {"score": score, "confidence": 0.9, "evidence": '"name: demo"'}
        path = self.root / f"{role}.json"
        path.write_text(json.dumps({"scores": {"correctness": dim(scores[0]), "clarity": dim(scores[1])},
                                    "pass_recommendation": "SHIP", "escalation": escalation, "veto": veto}))
        return self.script("check_gate.py", "--panel-receipt", role, str(path), *(["--synthesis"] if synthesis else []))

    def edit(self, n: int) -> None:
        (self.ws / "output" / "demo" / "SKILL.md").write_text(f"---\nname: demo\ndescription: Demo.\n---\n# Demo\n\nFix {n}.\n")

    def test_one_reviewer_is_the_whole_panel(self) -> None:
        self.gate()
        self.assertEqual(self.review((9, 9)).returncode, 0)
        out = self.script("check_gate.py", "--consensus")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("PANEL CONSENSUS: SHIP (allows SHIP); overall=0.900", out.stdout)
        consensus = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
        self.assertEqual((consensus["rounds"], consensus["disputed"]), (1, []))

    def test_each_profile_accepts_only_its_own_panel(self) -> None:
        self.gate()
        out = self.review((9, 9), role="verifier-a")
        self.assertEqual(out.returncode, 1)
        self.assertIn("is not one of reviewer (this build's lite profile panel; it is a member of the full", out.stdout)
        (self.ws / "work" / "manifest.yaml").write_text("profile: full\n")
        self.gate()
        out = self.review((9, 9))
        self.assertEqual(out.returncode, 1)
        self.assertIn("is not one of verifier-a, verifier-b, devils-advocate (this build's full profile panel; it is "
                      "the lite profile's one-member panel)", out.stdout)

    def test_no_synthesis_and_one_low_score_blocks(self) -> None:
        self.gate()
        self.assertEqual(self.review((2, 9)).returncode, 0)
        out = self.review((2, 9), synthesis=True)
        self.assertIn("no synthesis round", out.stdout)
        self.assertIn("PANEL CONSENSUS: BLOCK (allows UNSHIPPED)", self.script("check_gate.py", "--consensus").stdout)

    def test_the_reviewer_holds_the_escalation(self) -> None:
        self.gate()
        self.assertEqual(self.review((9, 9), escalation="ESCALATE: writes outside the workspace").returncode, 0)
        self.assertIn("PANEL CONSENSUS: ESCALATE", self.script("check_gate.py", "--consensus").stdout)
        rubric = self.ws / "work" / "evaluation" / "rubric.yaml"
        rubric.write_text(rubric.read_text() + "metric: answer-key\n")
        self.gate()
        veto = {"kind": "escalate", "evidence": '"name: demo"', "repro": "run it on input/gold-01.md"}
        out = self.review((9, 9), escalation="ESCALATE: unsafe", veto=veto)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("veto: reviewer escalate", self.script("check_gate.py", "--consensus").stdout)

    def test_the_panel_path_allows_one_iterate_then_ends(self) -> None:
        self.gate()
        self.assertEqual(self.review(self.LOW).returncode, 0)
        self.assertIn("PANEL CONSENSUS: ITERATE", self.script("check_gate.py", "--consensus").stdout)
        self.edit(1)
        self.gate()  # the panel-verdict path re-gates after an ITERATE
        self.assertEqual(self.review(self.LOW).returncode, 0)
        out = self.script("check_gate.py", "--consensus").stdout
        self.assertIn("PANEL CONSENSUS: ITERATE", out)
        record = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
        self.assertEqual((record["panel_round"], record["max_panel_rounds"]), (2, 2))
        self.edit(2)
        self.gate()
        out = self.review((9, 9))
        self.assertEqual(out.returncode, 1)
        self.assertIn("PANEL ROUNDS EXHAUSTED: the panel ran its 2 rounds", out.stdout)


class LiteNextTest(test_metric_first.MetricFirstFixture):
    """--next tells a lite build to dispatch one reviewer, never the three members."""

    def test_next_dispatches_one_reviewer(self) -> None:
        path = self.ws / "work" / "evaluation" / "evaluate.sh"
        path.write_text(test_rescore.EVALUATE)
        path.chmod(0o755)
        self.log(0.95)
        (self.ws / "work" / "manifest.yaml").write_text("profile: lite\n")
        self.assertEqual(self.run_script("gate.py", "--model", "m", CSA_PROFILE="").returncode, 0)
        out = self.run_script("check_gate.py", "--next", CSA_PROFILE="").stdout
        self.assertIn("dispatch ONE reviewer subagent, role `reviewer`", out)
        self.assertIn(str(SKILL / "references" / "panel" / "reviewer.md"), out)
        self.assertIn("--panel-receipt reviewer", out)
        self.assertNotIn("verifier-a", out)


class FullProfileUnchangedTest(TempWorkspace):
    def test_the_receipts_digest_is_the_one_older_consensus_records_hold(self) -> None:
        """consensus.json binds the receipts by a digest over the three roles; the lite role must not enter it, or
        every consensus written before the lite profile reads as stale (agent-skills fidelity fixtures)."""
        import hashlib
        import panel
        self.make_build(["gold-01"])
        folder = self.ws / "work" / "experiments" / "panel"
        folder.mkdir(parents=True)
        for role in (*panel.ROLES, panel.REVIEWER):
            (folder / f"{role}.json").write_text(f'{{"role": "{role}"}}')
        old = hashlib.sha256()
        for role in ("verifier-a", "verifier-b", "devils-advocate"):
            for synthesis in (False, True):
                path = folder / f"{role}{'.synthesis' if synthesis else ''}.json"
                old.update(f"{role}{synthesis}".encode() + (gate_common.file_sha256(path) or "-").encode())
        with mock.patch.dict(os.environ, {"CSA_PROFILE": ""}):
            self.assertEqual(panel._receipts_digest(self.ws), old.hexdigest())


class LiteRulesTest(unittest.TestCase):
    def test_the_numbers_live_in_verdict_rules(self) -> None:
        lite = json.loads((SKILL / "references" / "verdict-rules.json").read_text())["lite"]
        self.assertEqual((lite["runs_per_arm"], lite["max_iterations"], lite["plateau_window"],
                          lite["max_panel_rounds"], lite["panel"]), (2, 3, 2, 2, ["reviewer"]))
        self.assertGreaterEqual(lite["runs_per_arm"], 2, "never a 1-run gate")

    def test_skill_md_names_the_lite_profile_near_the_top(self) -> None:
        lines = (SKILL / "SKILL.md").read_text().splitlines()
        top = next(i for i, line in enumerate(lines) if line.startswith("| Phase |"))
        [line] = [x for x in lines[:top] if "--profile lite" in x]
        self.assertIn("CSA_PROFILE=lite", line)
        self.assertIn("Small or fast model", line)


if __name__ == "__main__":
    unittest.main()
