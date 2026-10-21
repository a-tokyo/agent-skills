"""Offline tests for panel.py through check_gate.py: role receipts, consensus and the scoped re-check.

Fixtures (fake `claude`, fake adapters, temp workspaces) are in helpers.py. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

from helpers import SCRIPTS, TempWorkspace
import gate_common  # noqa: E402  (on sys.path through helpers)


class PanelTest(TempWorkspace):
    def setUp(self) -> None:
        super().setUp()
        self.make_build(["gold-01", "trigger-1"])
        self.write_rubric()

    def gate(self) -> None:
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)

    def consensus(self) -> subprocess.CompletedProcess:
        return self.run_script("check_gate.py", "--consensus")

    def test_no_receipt_before_a_valid_gate(self) -> None:
        out = self.receipt("verifier-a", 9, 9)
        self.assertEqual(out.returncode, 1)
        self.assertIn("GATE INVALID", out.stdout)
        self.assertFalse((self.ws / "work" / "experiments" / "panel").exists())

    def test_receipts_are_checked_against_the_rubric(self) -> None:
        self.gate()
        self.assertIn("is not one of verifier-a", self.receipt("verifier-c", 9, 9).stdout)
        self.assertIn("not on the rubric scale 1-10", self.receipt("verifier-a", 11, 9).stdout)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"scores": {"correctness": {"score": 9, "confidence": 0.9, "evidence": "q"}}}))
        out = self.run_script("check_gate.py", "--panel-receipt", "verifier-a", str(bad))
        self.assertIn("cover exactly the rubric's dimensions ['clarity', 'correctness']", out.stdout)
        bad.write_text(json.dumps({"scores": {"correctness": {"score": 9, "confidence": 0.9},
                                              "clarity": {"score": 5, "confidence": 0.9}}}))
        out = self.run_script("check_gate.py", "--panel-receipt", "verifier-a", str(bad))
        self.assertIn("correctness: a score of 9 needs evidence", out.stdout)
        out = self.receipt("verifier-a", 9, 9)
        self.assertEqual(out.returncode, 0, out.stdout)
        receipt = json.loads((self.ws / "work" / "experiments" / "panel" / "verifier-a.json").read_text())
        self.assertEqual(receipt["skill_sha256"], gate_common.skill_sha256(self.ws, "demo"))
        self.assertEqual(receipt["rubric_sha256"],
                         gate_common.file_sha256(self.ws / "work" / "evaluation" / "rubric.yaml"))
        self.assertEqual((receipt["round"], receipt["recommendation"]), (1, "SHIP WITH CAVEATS"))
        self.assertIn("no current role receipt from verifier-b, devils-advocate", self.consensus().stdout)

    def test_a_dispute_needs_a_synthesis_round_then_resolves_by_majority(self) -> None:
        self.gate()
        for role, scores in (("verifier-a", (9, 9)), ("verifier-b", (9, 9)), ("devils-advocate", (6, 9))):
            self.assertEqual(self.receipt(role, *scores).returncode, 0)
        out = self.consensus()
        self.assertEqual(out.returncode, 1)
        self.assertIn("SYNTHESIS NEEDED on correctness", out.stdout)
        self.assertIn("may change only the disputed dimensions ['correctness']; got ['clarity']",
                      self.receipt("verifier-a", 0, 0, synthesis={"clarity": {"revised": 7}}).stdout)
        for role, revised in (("verifier-a", 9), ("verifier-b", 8), ("devils-advocate", 6)):
            out = self.receipt(role, 0, 0, synthesis={"correctness": {"original": 0, "revised": revised}})
            self.assertEqual(out.returncode, 0, out.stdout)
        line = self.consensus().stdout
        self.assertIn("PANEL CONSENSUS: SHIP (allows SHIP); overall=0.870", line,
                      "correctness: 9 and 8 agree (8.5), the 6 is logged dissent; clarity 9")
        data = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
        self.assertEqual((data["rounds"], data["disputed"]), (2, ["correctness"]))
        self.assertIn("devils-advocate 6", data["dissent"]["correctness"])

    def test_revised_scores_meet_the_first_rounds_scale_and_evidence_rules(self) -> None:
        self.gate()
        for role in ("verifier-a", "verifier-b"):
            self.assertEqual(self.receipt(role, 9, 9).returncode, 0)
        da, first = "devils-advocate", self.root / "da.json"
        first.write_text(json.dumps({"scores": {"correctness": {"score": 6, "confidence": 0.9, "evidence": ""},
                                                "clarity": {"score": 9, "confidence": 0.9, "evidence": "q"}}}))
        self.assertEqual(self.run_script("check_gate.py", "--panel-receipt", da, str(first)).returncode, 0)
        for revised in (0.5, 11, 100, "9", None, True):
            with self.subTest(revised=revised):
                out = self.receipt(da, 0, 0, synthesis={"correctness": {"revised": revised, "justification": "j"}})
                self.assertEqual(out.returncode, 1, out.stdout)
                self.assertIn(f"correctness: revised score {revised!r} is not on the rubric scale 1-10", out.stdout)
        for revised in (1, 10):
            with self.subTest(empty_evidence_at=revised):
                out = self.receipt(da, 0, 0, synthesis={"correctness": {"revised": revised, "justification": " "}})
                self.assertIn(f"correctness: a revised score of {revised} needs evidence", out.stdout)
        for revised, body in ((1, {"evidence": "the failing line"}), (10, {"justification": "quote"}), (5, {})):
            with self.subTest(accepted=revised):
                out = self.receipt(da, 0, 0, synthesis={"correctness": {"revised": revised, **body}})
                self.assertEqual(out.returncode, 0, out.stdout)
        out = self.receipt("verifier-a", 0, 0, synthesis={"correctness": {"revised": 10}})
        self.assertEqual(out.returncode, 0, "the first round's evidence backs a revised extreme score")

    def test_a_deadlock_after_synthesis_escalates(self) -> None:
        self.gate()
        for role, scores in (("verifier-a", (9, 9)), ("verifier-b", (6, 9)), ("devils-advocate", (3, 9))):
            self.receipt(role, *scores)
        for role, revised in (("verifier-a", 9), ("verifier-b", 6), ("devils-advocate", 3)):
            self.receipt(role, 0, 0, synthesis={"correctness": {"revised": revised}})
        line = self.consensus().stdout
        self.assertIn("PANEL CONSENSUS: ESCALATE (allows UNSHIPPED); overall=n/a", line)
        self.assertIn("deadlocked: correctness", line)

    ITERATE_PANEL = {"verifier-a": (5, 5), "verifier-b": (5, 6), "devils-advocate": (5, 5)}

    def fix_and_regate(self, n: int) -> None:
        (self.ws / "output" / "demo" / "SKILL.md").write_text(f"---\nname: demo\ndescription: Fix {n}.\n---\n# Demo\n")
        self.gate()

    def finish(self, text: str) -> dict:
        (self.ws / "BENCHMARK.md").write_text(text)
        return json.loads(self.run_script("check_gate.py", "--finish", "--json").stdout)

    def test_a_round_is_a_consensus_over_a_new_skill_and_the_cap_ends_the_build(self) -> None:
        self.gate()
        lines = []
        for n in (1, 2, 3):
            if n > 1:
                self.fix_and_regate(n)
            line = self.run_panel(self.ITERATE_PANEL)
            self.assertIn("PANEL CONSENSUS: ITERATE (allows UNSHIPPED)", line)
            consensus = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
            self.assertEqual((consensus["panel_round"], consensus["max_panel_rounds"]), (n, 3))
            lines.append(line)
        self.assertEqual(self.run_panel(self.ITERATE_PANEL), lines[-1], "a re-run on the same skill is the same round")
        rounds = self.ws / "work" / "experiments" / "panel" / "rounds"
        self.assertEqual(sorted(p.name for p in rounds.iterdir()), ["1", "2", "3"])
        record = json.loads((rounds / "2" / "round.json").read_text())
        self.assertEqual((record["round"], record["label"], record["line"]), (2, "ITERATE", lines[1]))
        self.assertEqual(set(record["files"]), {"SKILL.md"})
        self.assertIn("Fix 2.", (rounds / "2" / "SKILL.md").read_text())
        self.assertEqual(sorted(p.name for p in (rounds / "3").iterdir()),
                         ["SKILL.md", "devils-advocate.json", "round.json", "verifier-a.json", "verifier-b.json"])
        self.fix_and_regate(4)
        for out in (self.run_script("check_gate.py", "--panel"), self.receipt("verifier-a", 9, 9), self.consensus()):
            self.assertEqual(out.returncode, 1, out.stdout)
            self.assertIn("PANEL ROUNDS EXHAUSTED: the panel ran its 3 rounds", out.stdout)
            self.assertIn(f"write `Verdict: UNSHIPPED` in BENCHMARK.md beside the last round's line: {lines[-1]}",
                          out.stdout)
        gate_line = self.run_script("check_gate.py").stdout.strip()
        data = self.finish(f"Verdict: UNSHIPPED\n\n{gate_line}\n")
        self.assertFalse(data["finished"])
        self.assertIn("lacks the last round's PANEL CONSENSUS line", data["reason"])
        self.assertIn("needs the panel's consensus on this skill: the panel ran its 3 rounds", self.finish(f"Verdict: SHIP WITH CAVEATS\n\n{gate_line}\n{lines[-1]}\n")
                      ["reason"], "no SHIP on a skill the capped panel never scored")
        self.assertEqual(self.finish(f"Verdict: UNSHIPPED\n\n{gate_line}\n{lines[-1]}\n"),
                         {"finished": True, "verdict": "UNSHIPPED", "reason": "ok"})

    def test_full_recheck_is_the_default(self) -> None:
        rules = json.loads((SCRIPTS.parent / "references" / "verdict-rules.json").read_text())
        self.assertEqual((rules["post_panel_recheck"], rules["scoped_max_changed_lines"]), ("full", 40))
        self.gate()
        self.run_panel(self.ITERATE_PANEL)
        self.fix_and_regate(2)
        out = self.run_script("check_gate.py", "--panel")
        self.assertIn("PANEL ROUND 2: full: each role scores every dimension (post_panel_recheck is full)", out.stdout)
        partial = self.root / "partial.json"
        partial.write_text(json.dumps({"scores": {"clarity": {"score": 9, "confidence": 0.9, "evidence": "q"}}}))
        out = self.run_script("check_gate.py", "--panel-receipt", "verifier-a", str(partial))
        self.assertIn("scores must cover exactly the rubric's dimensions ['clarity', 'correctness']", out.stdout)
        self.assertEqual(self.receipt("verifier-a", 9, 9).returncode, 0)
        receipt = json.loads((self.ws / "work" / "experiments" / "panel" / "verifier-a.json").read_text())
        self.assertNotIn("recheck", receipt, "full receipts keep today's shape")

    def test_a_ship_on_the_last_round_still_ships(self) -> None:
        self.gate()
        for n in (1, 2):
            if n > 1:
                self.fix_and_regate(n)
            self.run_panel(self.ITERATE_PANEL)
        self.fix_and_regate(3)
        line = self.run_panel({"verifier-a": (9, 9), "verifier-b": (9, 8), "devils-advocate": (8, 8)})
        self.assertIn("PANEL CONSENSUS: SHIP (allows SHIP)", line)
        self.assertEqual(json.loads((self.ws / "work" / "experiments" / "panel" / "rounds" / "3" / "round.json")
                                    .read_text())["line"], line)
        gate_line = self.run_script("check_gate.py").stdout.strip()
        self.assertTrue(self.finish(f"Verdict: SHIP\n\n{gate_line}\n{line}\n")["finished"])

    def test_the_thresholds_come_from_verdict_rules(self) -> None:
        rules = json.loads((SCRIPTS.parent / "references" / "verdict-rules.json").read_text())
        self.assertEqual((rules["caveat_margin"], rules["block_below"]), (0.10, 3))
        self.write_rubric(target=0.95)
        self.gate()
        line = self.run_panel({"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (8, 8)})
        self.assertIn("PANEL CONSENSUS: SHIP WITH CAVEATS (allows SHIP WITH CAVEATS); overall=0.867 target=0.95",
                      line, "rubric.yaml's target_score, minus caveat_margin, sets the band")


SECTIONED_SKILL = textwrap.dedent("""\
    ---
    name: demo
    description: Demo.
    ---
    # Demo
    intro
    ## Workflow
    step one
    step two
    ```
    ## Examples inside a fence is not a heading
    ```
    ## Examples
    example one
    ## Hard constraints
    never X
    """)


class ScopedRecheckTest(TempWorkspace):
    """post_panel_recheck: scoped, on a copy of the skill whose verdict-rules.json says so."""

    def setUp(self) -> None:
        super().setUp()
        skill = self.root / "skill"
        for part in ("scripts", "references"):
            shutil.copytree(SCRIPTS.parent / part, skill / part, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        rules = json.loads((skill / "references" / "verdict-rules.json").read_text(encoding="utf-8"))
        (skill / "references" / "verdict-rules.json").write_text(json.dumps({**rules, "post_panel_recheck": "scoped"}))
        self.scripts = skill / "scripts"
        self.make_build(["gold-01", "trigger-1"])
        self.skill_md = self.ws / "output" / "demo" / "SKILL.md"
        self.skill_md.write_text(SECTIONED_SKILL)
        self.write_sectioned_rubric()
        self.gate()

    def write_sectioned_rubric(self, target: float = 0.85, correctness: str = "[Workflow]") -> None:
        (self.ws / "work" / "evaluation" / "rubric.yaml").write_text(textwrap.dedent(f"""\
            dimensions:
              - name: correctness
                weight: 0.6
                sections: {correctness}
              - name: clarity
                weight: 0.4
                sections: ["Examples"]
            target_score: {target}
            """))

    def gate(self) -> None:
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def edit(self, old: str, new: str) -> None:
        text = self.skill_md.read_text()
        self.assertIn(old, text)
        self.skill_md.write_text(text.replace(old, new, 1))
        self.gate()

    def panel_line(self) -> str:
        out = self.run_script("check_gate.py", "--panel")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout.strip().splitlines()[-1]

    def clarity_receipt(self, role: str, clarity: float) -> subprocess.CompletedProcess:
        path = self.root / f"{role}-scoped.json"
        path.write_text(json.dumps({"scores": {"clarity": {"score": clarity, "confidence": 0.9, "evidence": "q"}},
                                    "pass_recommendation": "ITERATE"}))
        return self.run_script("check_gate.py", "--panel-receipt", role, str(path))

    def receipt_file(self, role: str, round_: int | None = None) -> Path:
        panel_dir = self.ws / "work" / "experiments" / "panel"
        return (panel_dir / "rounds" / str(round_) if round_ else panel_dir) / f"{role}.json"

    ROUND_1 = {"verifier-a": (5, 5), "verifier-b": (6, 5), "devils-advocate": (5, 5)}

    def test_a_scoped_round_rescores_only_the_touched_dimensions_and_carries_the_rest(self) -> None:
        self.assertIn("PANEL ROUND 1: full: each role scores every dimension (the first panel round)", self.panel_line())
        self.run_panel(self.ROUND_1)
        round_1_sha = gate_common.skill_sha256(self.ws, "demo")
        self.edit("example one", "example one, with the edge case")
        self.assertEqual(self.panel_line(), "PANEL ROUND 2: scoped re-check: each role scores only clarity; "
                                            "correctness carry forward from round 1")
        out = self.receipt("verifier-a", 9, 9)
        self.assertEqual(out.returncode, 1)
        self.assertIn("score exactly ['clarity']; correctness carry forward from round 1; got ['clarity', 'correctness']",
                      out.stdout)
        for role in ("verifier-a", "verifier-b", "devils-advocate"):
            out = self.clarity_receipt(role, 9)
            self.assertEqual(out.returncode, 0, out.stdout)
        receipt = json.loads(self.receipt_file("verifier-b").read_text())
        self.assertEqual(receipt["scores"]["correctness"],
                         {"score": 6, "confidence": 0.9, "evidence": "verbatim quote", "carried_from_sha256": round_1_sha})
        self.assertEqual(receipt["skill_sha256"], gate_common.skill_sha256(self.ws, "demo"), "bound to the new skill")
        self.assertEqual(receipt["recheck"], {"mode": "scoped", "rescored": ["clarity"], "carried": ["correctness"],
                                              "from_round": 1, "from_skill_sha256": round_1_sha})
        archived = self.receipt_file("verifier-a", 1)
        original = archived.read_text()
        archived.write_text(original.replace('"score": 5', '"score": 7', 1))
        self.assertIn("verifier-a's carried scores differ from round 1's archived receipt",
                      self.run_script("check_gate.py", "--consensus").stdout)
        archived.write_text(original)
        current = self.receipt_file("devils-advocate")
        data = json.loads(current.read_text())
        data["scores"]["correctness"]["score"] = 9
        current.write_text(json.dumps(data))
        self.assertIn("devils-advocate's carried scores differ", self.run_script("check_gate.py", "--consensus").stdout)
        self.assertEqual(self.clarity_receipt("devils-advocate", 9).returncode, 0)
        out = self.run_script("check_gate.py", "--consensus")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("PANEL CONSENSUS: ITERATE (allows UNSHIPPED); overall=0.680",
                      out.stdout, "the label comes from the merged scores: correctness 5.33 carried, clarity 9")
        consensus = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
        self.assertEqual((consensus["panel_round"], consensus["recheck"]["mode"]), (2, "scoped"))
        record = json.loads((self.ws / "work" / "experiments" / "panel" / "rounds" / "2" / "round.json").read_text())
        self.assertEqual(record["recheck"]["carried"], ["correctness"])

    def test_a_carried_dispute_is_not_reopened(self) -> None:
        for role, scores in (("verifier-a", (9, 5)), ("verifier-b", (9, 5)), ("devils-advocate", (6, 5))):
            self.assertEqual(self.receipt(role, *scores).returncode, 0)
        for role, revised in (("verifier-a", 9), ("verifier-b", 8), ("devils-advocate", 6)):
            self.assertEqual(self.receipt(role, 0, 0, synthesis={"correctness": {"revised": revised}}).returncode, 0)
        self.assertEqual(self.run_script("check_gate.py", "--consensus").returncode, 0)
        self.edit("example one", "example one, clearer")
        for role in ("verifier-a", "verifier-b", "devils-advocate"):
            self.assertEqual(self.clarity_receipt(role, 8).returncode, 0)
        out = self.run_script("check_gate.py", "--consensus")
        self.assertEqual(out.returncode, 0, "correctness 9/8/6 is carried from round 1's synthesis, not disputed again")
        consensus = json.loads((self.ws / "work" / "experiments" / "panel" / "consensus.json").read_text())
        self.assertEqual((consensus["disputed"], consensus["rounds"]), ([], 1))
        self.assertIn("devils-advocate 6", consensus["dissent"]["correctness"])

    def test_large_frontmatter_constraint_and_other_file_changes_fall_back_to_full(self) -> None:
        self.run_panel(self.ROUND_1)
        extra = self.ws / "output" / "demo" / "references" / "extra.md"
        cases = (
            (lambda: self.edit("description: Demo.", "description: Demo, sharper."), "the frontmatter changed"),
            (lambda: self.edit("never X", "never Y"),
             "the change touches a section under ['constraint'] (scoped_full_headings)"),
            (lambda: self.edit("example one\n", "example one\n" + "more\n" * 41),
             "41 changed lines since round 1 (scoped_max_changed_lines 40)"),
            (lambda: (extra.parent.mkdir(), extra.write_text("x"), self.gate()),
             "files other than SKILL.md changed since round 1 (references/extra.md)"),
            (lambda: (self.edit("step one", "step 1"), self.edit("example one", "example 1")),
             "the change touches every dimension"),
            (lambda: self.edit("intro", "introduction"), "the change touches no section a dimension maps to"),
            (lambda: (self.write_sectioned_rubric(target=0.8), self.edit("example one", "example 1")),
             "the rubric changed since round 1"),
        )
        for change, why in cases:
            with self.subTest(why=why):
                change()
                self.assertEqual(self.panel_line(), f"PANEL ROUND 2: full: each role scores every dimension ({why})")
                out = self.clarity_receipt("verifier-a", 9)
                self.assertIn("scores must cover exactly the rubric's dimensions", out.stdout)
                shutil.rmtree(extra.parent, ignore_errors=True)
                self.skill_md.write_text(SECTIONED_SKILL)
                self.write_sectioned_rubric()
                self.gate()
        self.assertTrue(self.panel_line().startswith("PANEL ROUND 1: full"), "back on round 1's skill")

    def test_a_fenced_line_and_a_dimension_without_sections(self) -> None:
        self.run_panel(self.ROUND_1)
        self.edit("## Examples inside a fence", "## Examples, still inside a fence")
        self.assertEqual(self.panel_line(), "PANEL ROUND 2: scoped re-check: each role scores only correctness; "
                                            "clarity carry forward from round 1", "the fence sits under Workflow")
        self.skill_md.write_text(SECTIONED_SKILL)
        self.write_sectioned_rubric(correctness="Workflow")
        self.gate()
        out = self.run_script("check_gate.py", "--panel")
        self.assertIn("sections must be a one-line flow list", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
