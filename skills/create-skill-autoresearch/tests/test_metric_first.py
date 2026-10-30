"""Offline tests for metric-first shipping (verdict-rules.json metric_first): with `metric: answer-key` in
rubric.yaml the gate and the Phase 4 metric decide the verdict, the panel runs once as a review and caps it only
on a cited hard defect; without it the panel's verdict path is unchanged.

Fixtures (fake `claude`, temp workspaces) are in helpers.py. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import subprocess
import unittest

from helpers import TempWorkspace

REVIEW_LOW = {"verifier-a": (5, 5), "verifier-b": (5, 6), "devils-advocate": (5, 5)}  # an ITERATE on the old path
CITED = {"evidence": '"name: demo"', "repro": "run the skill on input/gold-01.md: it writes outside the workspace"}


class MetricFirstTest(TempWorkspace):
    def setUp(self) -> None:
        super().setUp()
        self.make_build(["gold-01", "trigger-1"])
        self.rubric("answer-key")
        self.git("init", "-q")
        self.git("add", "output")
        self.git("commit", "-q", "-m", "baseline")
        self.commit = self.git("rev-parse", "HEAD")

    def rubric(self, metric: str | None) -> None:
        self.write_rubric(target=0.85)
        if metric:
            path = self.ws / "work" / "evaluation" / "rubric.yaml"
            path.write_text(path.read_text() + f"metric: {metric}\n")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=t", "-c", "user.email=t@t",
                               "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def log(self, metric: float, *extra: dict) -> None:
        path = self.ws / "work" / "experiments" / "autoresearch.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [{"experiment": 0, "commit": self.commit, "metric": metric, "status": "baseline"}, *extra]
        path.write_text("".join(json.dumps(e) + "\n" for e in lines))

    def gate(self) -> None:
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def submit(self, role: str, scores: tuple[float, float], escalation: str | None = None,
               veto: dict | None = None) -> subprocess.CompletedProcess:
        def dim(score: float) -> dict:
            return {"score": score, "confidence": 0.9, "evidence": '"name: demo"'}
        path = self.root / f"{role}.json"
        path.write_text(json.dumps({"scores": {"correctness": dim(scores[0]), "clarity": dim(scores[1])},
                                    "pass_recommendation": "ITERATE", "escalation": escalation, "veto": veto}))
        return self.run_script("check_gate.py", "--panel-receipt", role, str(path))

    def panel(self, scores: dict = REVIEW_LOW, **extra: dict) -> list[str]:
        """Every role's receipt (extra: role -> submit kwargs), then --consensus; returns its output lines."""
        for role, pair in scores.items():
            out = self.submit(role, pair, **extra.get(role, {}))
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        out = self.run_script("check_gate.py", "--consensus")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout.strip().splitlines()

    def finish(self, verdict: str, *lines: str) -> dict:
        gate_line = self.run_script("check_gate.py").stdout.strip()
        (self.ws / "BENCHMARK.md").write_text(f"Verdict: {verdict}\n\n{gate_line}\n" + "".join(f"{x}\n" for x in lines))
        return json.loads(self.run_script("check_gate.py", "--finish", "--json").stdout)

    def test_checkable_ship_stands_on_the_gate_and_metric_not_the_panels_scores(self) -> None:
        self.log(0.91)
        self.gate()
        consensus, rule = self.panel()
        self.assertIn("PANEL CONSENSUS: ITERATE (review; veto: none); overall=0.513", consensus)
        self.assertEqual(rule, "SHIP RULE: SHIP (metric-first); gate: every gold delta > 0; "
                               "metric: experiment 0 0.910 -> 0.910 target 0.85; veto: none")
        data = self.finish("SHIP", consensus)
        self.assertFalse(data["finished"])
        self.assertIn("lacks check_gate.py's current SHIP RULE line", data["reason"])
        self.assertEqual(self.finish("SHIP", consensus, rule), {"finished": True, "verdict": "SHIP", "reason": "ok"})
        record = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
        self.assertEqual((record["checkable"], record["max_panel_rounds"], record["allowed"]), (True, 1, "SHIP"))
        loop = self.run_script("check_gate.py", "--loop").stdout
        self.assertTrue(loop.startswith("LOOP DONE:"), "a review's ITERATE never reopens the Phase 4 loop: " + loop)

    def test_one_review_round_then_a_changed_skill_ends_unshipped(self) -> None:
        self.log(0.91)
        self.gate()
        consensus, _ = self.panel()
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Edited.\n---\n# Demo\n")
        self.gate()
        out = self.run_script("check_gate.py", "--panel")
        self.assertEqual(out.returncode, 1)
        self.assertIn("PANEL ROUNDS EXHAUSTED: the panel ran its 1 rounds", out.stdout)
        self.assertIn("lacks the last round's PANEL CONSENSUS line", self.finish("UNSHIPPED")["reason"])
        self.assertTrue(self.finish("UNSHIPPED", consensus)["finished"])

    def test_checkable_below_target_caps_or_unships(self) -> None:
        for metric, validation, allowed in ((0.80, None, "SHIP WITH CAVEATS"), (0.70, None, "UNSHIPPED"),
                                            (0.95, 0.70, "UNSHIPPED"), (0.95, 0.80, "SHIP WITH CAVEATS")):
            with self.subTest(metric=metric, validation=validation):
                split = self.ws / "work" / "evaluation" / "data-split.yaml"
                text = split.read_text().replace('validation: ["input/v.md"]\n', "")
                checks = []
                if validation is not None:
                    text += 'validation: ["input/v.md"]\n'
                    checks = [{"type": "validation_check", "experiment": 0, "training_score": metric,
                               "validation_score": validation}]
                split.write_text(text)
                self.log(metric, *checks)
                self.gate()
                consensus, rule = self.panel({"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (9, 9)})
                self.assertTrue(rule.startswith(f"SHIP RULE: {allowed} (metric-first)"), rule)
                data = self.finish("SHIP", consensus, rule)
                self.assertFalse(data["finished"])
                self.assertIn(f"the metric-first rule allows at most {allowed}", data["reason"])
                self.assertTrue(self.finish(allowed, consensus, rule)["finished"])

    def test_a_metric_from_another_skill_state_ships_nothing(self) -> None:
        self.log(0.95)
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Unscored.\n---\n# Demo\n")
        self.gate()
        _, rule = self.panel()
        self.assertTrue(rule.startswith("SHIP RULE: UNSHIPPED (metric-first)"), rule)
        self.assertIn("no kept experiment in autoresearch.jsonl scored output/demo/ as it is now", rule)

    def test_a_gold_delta_at_or_below_zero_caps_the_gate(self) -> None:
        # trigout-* is a gold case at delta 0 (helpers.FAKE_CLAUDE)
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "trigout-1").mkdir()
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "trigout-1" / "prompt.md").write_text("Do it.\n")
        split = self.ws / "work" / "evaluation" / "data-split.yaml"
        split.write_text(split.read_text() + '  - "trigout-1"\n')
        self.log(0.95)
        self.gate()
        consensus, rule = self.panel()
        self.assertEqual(rule, "SHIP RULE: SHIP WITH CAVEATS (metric-first); gate: gold delta<=0 on trigout-1, "
                               "lowest +0.000; metric: experiment 0 0.950 -> 0.950 target 0.85; veto: none")
        self.assertIn("allows at most SHIP WITH CAVEATS", self.finish("SHIP", consensus, rule)["reason"])

    def test_a_gold_delta_below_the_caveat_margin_unships(self) -> None:
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "regress-25").mkdir()
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "regress-25" / "prompt.md").write_text("Do it.\n")
        split = self.ws / "work" / "evaluation" / "data-split.yaml"
        split.write_text(split.read_text() + '  - "regress-25"\n')
        self.log(0.95)
        self.gate()
        consensus, rule = self.panel()
        self.assertEqual(rule, "SHIP RULE: UNSHIPPED (metric-first); gate: gold delta<=0 on regress-25, "
                               "lowest -0.250; metric: experiment 0 0.950 -> 0.950 target 0.85; veto: none")
        self.assertIn("allows at most UNSHIPPED", self.finish("SHIP WITH CAVEATS", consensus, rule)["reason"])
        self.assertTrue(self.finish("UNSHIPPED", consensus, rule)["finished"])

    def test_a_cited_hard_defect_vetoes(self) -> None:
        self.log(0.95)
        self.gate()
        bad = self.submit("verifier-a", (9, 9), veto={"kind": "hard_constraint", "evidence": '"an invented line"',
                                                      "repro": "wc -l"})
        self.assertEqual(bad.returncode, 1)
        self.assertIn("veto: the evidence quote 'an invented line' is not in output/<skill>/ or input/", bad.stdout)
        self.assertIn("needs `evidence` (a verbatim quote) and `repro`",
                      self.submit("verifier-a", (9, 9), veto={"kind": "safety", "evidence": '"name: demo"'}).stdout)
        self.assertIn("veto must be null or", self.submit("verifier-a", (9, 9), veto={"kind": "taste"}).stdout)
        high = {"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (9, 9)}
        consensus, rule = self.panel(high, **{"verifier-b": {"veto": {"kind": "safety", **CITED}}})
        self.assertIn("PANEL CONSENSUS: SHIP (review; veto: verifier-b safety)", consensus)
        self.assertEqual(rule, "SHIP RULE: UNSHIPPED (metric-first); gate: every gold delta > 0; "
                               "metric: experiment 0 0.950 -> 0.950 target 0.85; veto: verifier-b safety")
        self.assertIn("allows at most UNSHIPPED", self.finish("SHIP WITH CAVEATS", consensus, rule)["reason"])
        self.assertTrue(self.finish("UNSHIPPED", consensus, rule)["finished"])

    def test_escalate_vetoes_only_with_a_citation(self) -> None:
        self.log(0.95)
        self.gate()
        high = {"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (9, 9)}
        consensus, rule = self.panel(high, **{"devils-advocate": {"escalation": "ESCALATE: feels risky"}})
        self.assertIn("PANEL CONSENSUS: ESCALATE (review; veto: none)", consensus)
        self.assertTrue(rule.startswith("SHIP RULE: SHIP (metric-first)"), "an uncited ESCALATE is logged, not a veto")
        self.assertIn("an escalate veto is the Devil's Advocate's",
                      self.submit("verifier-a", (9, 9), veto={"kind": "escalate", **CITED}).stdout)
        self.assertIn("an escalate veto is the Devil's Advocate's",
                      self.submit("devils-advocate", (9, 9), veto={"kind": "escalate", **CITED}).stdout)
        consensus, rule = self.panel(high, **{"devils-advocate": {"escalation": "ESCALATE: writes outside the workspace",
                                                                  "veto": {"kind": "escalate", **CITED}}})
        self.assertIn("(review; veto: devils-advocate escalate)", consensus)
        self.assertTrue(rule.startswith("SHIP RULE: UNSHIPPED (metric-first)"), rule)
        self.assertTrue(self.finish("UNSHIPPED", consensus, rule)["finished"])

    def test_the_panel_verdict_path_is_unchanged_without_an_answer_key(self) -> None:
        for metric in (None, "llm-judge"):
            with self.subTest(metric=metric):
                self.rubric(metric)
                self.log(0.95)
                self.gate()
                lines = self.panel()
                self.assertEqual(len(lines), 1, "no SHIP RULE line on the panel-verdict path")
                self.assertIn("PANEL CONSENSUS: ITERATE (allows UNSHIPPED)", lines[0])
                record = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
                self.assertEqual((record["checkable"], record["max_panel_rounds"]), (False, 3))
                self.assertIn("the panel's consensus allows at most UNSHIPPED", self.finish("SHIP", lines[0])["reason"])
        self.rubric("vibes")
        self.assertIn("metric 'vibes' is not one of answer-key, llm-judge",
                      self.run_script("check_gate.py", "--fast-path").stdout)


if __name__ == "__main__":
    unittest.main()
