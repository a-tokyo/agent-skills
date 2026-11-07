"""Offline tests for fix cycles after the panel (scripts/fix_cycle.py, SKILL.md 5.4): a consensus that ends the
build UNSHIPPED on cited findings opens a cycle instead; the one fix commit may touch only declared lines; the
re-check clears resolved findings from the veto; an unresolved one keeps it; max_fix_cycles caps the cycles; outside
a cycle a post-panel edit still cannot re-gate (A3).

Fixtures (fake `claude`, temp workspaces) are in helpers.py and test_metric_first.py. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import subprocess
import unittest

from test_metric_first import CITED, MetricFirstFixture  # helpers.py puts scripts/ on sys.path

from gate_common import skill_sha256  # noqa: E402

HIGH = {"verifier-a": (9, 9), "verifier-b": (9, 9), "devils-advocate": (9, 9)}
FIXED = "Pass item text to scripts in a file, never inside a shell command.\n"
SCENARIO = {"scenario": "item text runs as shell", "severity": "critical", "evidence": '"name: demo"',
            "repro": "an item line holding $(touch pwned)"}


class FixCycleFixture(MetricFirstFixture):
    env: dict = {}

    def setUp(self) -> None:
        super().setUp()
        (self.ws / "work" / "evaluation" / "evaluate.sh").write_text("echo METRIC overall_score=0.95\n")

    def run_script(self, name, *args, **kwargs):  # every call under this fixture's profile
        return super().run_script(name, *args, **{**self.env, **kwargs})

    def score(self, role: str, scores: tuple[float, float], scenarios: list | None = None, **extra) -> None:
        def dim(score: float) -> dict:
            return {"score": score, "confidence": 0.9, "evidence": '"name: demo"'}
        path = self.root / f"{role}.json"
        path.write_text(json.dumps({"scores": {"correctness": dim(scores[0]), "clarity": dim(scores[1])},
                                    "pass_recommendation": "ITERATE", "failure_scenarios": scenarios or [],
                                    "escalation": extra.get("escalation"), "veto": extra.get("veto")}))
        out = self.run_script("check_gate.py", "--panel-receipt", role, str(path))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def next(self) -> str:
        return self.run_script("check_gate.py", "--next").stdout

    def vetoed(self) -> None:
        """Metric-first at 0.95, the gate, and a full panel whose verifier-b files a cited safety veto."""
        self.log(0.95)
        self.gate()
        self.panel(HIGH, **{"verifier-b": {"veto": {"kind": "safety", **CITED}}})

    def open(self, n: int = 1) -> None:
        out = self.run_script("check_gate.py", "--fix-cycle")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn(f"FIX CYCLE {n} OPEN", out.stdout)

    def notes(self, n: int, lines: dict[str, str]) -> None:
        path = self.ws / "work" / "experiments" / f"fix-cycle-{n}.md"
        text = path.read_text()
        for fid, spans in lines.items():
            text = text.replace(f"## {fid}\n", f"## {fid}\n\nlines: {spans}\n", 1)
        path.write_text(text)

    def fix(self, n: int = 1, body: str = FIXED, spans: str = "SKILL.md:6-6", findings=("verifier-b.veto",),
            metric: float = 0.95) -> str:
        """Append `body` to SKILL.md, declare `spans` for each finding, commit once, and log its score (a rescore)."""
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text(skill.read_text() + body)
        self.notes(n, {fid: spans for fid in findings})
        self.git("add", "-A", "output")
        self.git("commit", "-q", "-m", f"fix cycle {n}")
        commit = self.git("rev-parse", "HEAD")
        self.relog(commit, metric)
        return commit

    def relog(self, commit: str, metric: float) -> None:
        path = self.ws / "work" / "experiments" / "autoresearch.jsonl"
        n = len(path.read_text().splitlines())
        with open(path, "a") as fh:
            fh.write(json.dumps({"experiment": n, "commit": commit, "metric": metric, "held_out": metric,
                                 "status": "rescore"}) + "\n")

    def recheck(self, role: str, results: dict) -> subprocess.CompletedProcess:
        path = self.root / f"{role}-recheck.json"
        path.write_text(json.dumps({"findings": results}))
        return self.run_script("check_gate.py", "--recheck-receipt", role, str(path))

    def resolved(self, quote: str = '"never inside a shell command"', regression: str | None = None) -> dict:
        return {"resolved": True, "evidence": quote, "regression": regression}

    def verdict_lines(self) -> tuple[str, list[str]]:
        out = self.next().splitlines()
        head = next(line for line in out if line.startswith("NEXT: 5.4: write BENCHMARK.md"))
        return head, [line for line in out if line.startswith(("GATE VALID", "PANEL CONSENSUS", "FIX CYCLE",
                                                               "SHIP RULE"))]


class FixCycleTest(FixCycleFixture):
    def test_a_cited_veto_opens_a_cycle_and_a_resolved_one_ships(self) -> None:
        self.vetoed()
        offer = self.next()
        self.assertIn("5.4 fix cycle 1 of 2", offer)
        self.assertIn("verifier-b.veto (safety)", offer)
        self.assertNotIn("Verdict: UNSHIPPED", offer)
        self.open()
        self.assertIn("fix ONLY verifier-b.veto", self.next())
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text(skill.read_text() + FIXED)
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3, "no re-gate before the one fix commit")
        self.assertIn("fix cycle 1 is open: no fix commit yet", out.stderr)
        self.notes(1, {"verifier-b.veto": "SKILL.md:6-6"})
        self.git("add", "-A", "output")
        self.git("commit", "-q", "-m", "fix cycle 1")
        self.assertIn("GATE INVALID: skill changed since the gate",
                      self.recheck("verifier-b", {"verifier-b.veto": self.resolved()}).stdout, "no re-check before the gate")
        self.assertIn("fix cycle 1: score the fixed skill", self.next())
        self.relog(self.git("rev-parse", "HEAD"), 0.95)
        self.assertIn("re-run the gate on the fixed skill", self.next())
        self.gate()
        nxt = self.next()
        self.assertIn("fix cycle 1 re-check: dispatch one subagent per role (verifier-b)", nxt)
        self.assertIn("references/panel/recheck.md", nxt)
        self.assertIn("a fix cycle never re-runs", self.run_script(
            "check_gate.py", "--panel-receipt", "verifier-a", str(self.root / "verifier-a.json")).stdout)
        self.assertIn("raised no finding", self.recheck("verifier-a", {}).stdout)
        self.assertIn("is not in output/<skill>/", self.recheck(
            "verifier-b", {"verifier-b.veto": self.resolved('"an invented fix"')}).stdout)
        out = self.recheck("verifier-b", {"verifier-b.veto": self.resolved()})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("resolved verifier-b.veto; unresolved none", out.stdout)
        receipt = json.loads((self.ws / "work/experiments/panel/fix-cycles/1/verifier-b.json").read_text())
        self.assertEqual(receipt["skill_sha256"], skill_sha256(self.ws, "demo"), "bound to the fixed skill")
        head, lines = self.verdict_lines()
        self.assertIn("`Verdict: SHIP`", head)
        self.assertIn("PANEL CONSENSUS: SHIP (review; veto: verifier-b safety)", lines[1], "the original record")
        self.assertEqual(lines[2], "FIX CYCLE 1 of 2: resolved verifier-b.veto; unresolved none")
        self.assertTrue(lines[3].startswith("SHIP RULE: SHIP (fix cycle 1); gate: every gold delta > 0;"), lines[3])
        self.assertTrue(lines[3].endswith("veto: none"), lines[3])
        self.assertIn("lacks this line", self.finish("SHIP", lines[1], lines[3])["reason"])
        self.assertEqual(self.finish("SHIP", *lines[1:]), {"finished": True, "verdict": "SHIP", "reason": "ok"})
        # A3 after the cycle: an edit to the re-checked skill can't re-gate and ends the build UNSHIPPED
        (self.ws / "BENCHMARK.md").unlink()
        skill.write_text(skill.read_text() + "More.\n")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3)
        self.assertIn("`Verdict: UNSHIPPED`", self.next())

    def test_a_fix_commit_touching_undeclared_lines_is_refused(self) -> None:
        self.vetoed()
        self.open()
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text(skill.read_text().replace("description: Demo.", "description: Demo, broader.") + FIXED)
        self.notes(1, {"verifier-b.veto": "SKILL.md:6-6"})
        self.git("add", "-A", "output")
        self.git("commit", "-q", "-m", "fix cycle 1")
        self.relog(self.git("rev-parse", "HEAD"), 0.95)
        nxt = self.next()
        self.assertIn("touches lines no finding in work/experiments/fix-cycle-1.md declares: SKILL.md:3", nxt)
        self.assertIn("reset -q --soft", nxt)
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("fix cycle 1 is open: the fix commit touches lines", out.stderr)
        self.notes(1, {"verifier-b.veto": "SKILL.md:3-3"})  # declaring a line makes it part of the fix
        self.assertIn("re-run the gate on the fixed skill", self.next())

    def test_two_commits_or_a_changed_gate_input_are_refused(self) -> None:
        self.vetoed()
        self.open()
        self.fix()
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text(skill.read_text() + "Second.\n")
        self.git("commit", "-q", "-am", "second")
        self.assertIn("2 commits touch output/demo/ since", self.next())
        self.git("reset", "-q", "--hard", "HEAD~1")
        (self.ws / "work" / "evaluation" / "rubric.yaml").write_text(
            (self.ws / "work" / "evaluation" / "rubric.yaml").read_text() + "# edited\n")
        self.assertIn("a gate input changed since fix cycle 1 opened", self.next())

    def test_an_unresolved_finding_keeps_the_veto_up_to_the_cap(self) -> None:
        self.vetoed()
        self.open()
        self.fix()
        self.gate()
        out = self.recheck("verifier-b", {"verifier-b.veto": self.resolved(regression="breaks the parser")})
        self.assertIn("unresolved verifier-b.veto", out.stdout, "a regression in the touched lines keeps the veto")
        offer = self.next()
        self.assertIn("5.4 fix cycle 2 of 2 (cycle 1 left verifier-b.veto unresolved)", offer)
        self.open(2)
        self.fix(2, body="Quote item text.\n", spans="SKILL.md:7-7")
        self.gate()
        self.recheck("verifier-b", {"verifier-b.veto": {"resolved": False, "evidence": None, "regression": None}})
        head, lines = self.verdict_lines()
        self.assertIn("`Verdict: UNSHIPPED`", head, "past max_fix_cycles the build ends UNSHIPPED")
        self.assertEqual(lines[2], "FIX CYCLE 2 of 2: resolved none; unresolved verifier-b.veto")
        self.assertTrue(lines[3].startswith("SHIP RULE: UNSHIPPED (fix cycle 2)"), lines[3])
        self.assertIn("allows at most UNSHIPPED", self.finish("SHIP", *lines[1:])["reason"])
        self.assertTrue(self.finish("UNSHIPPED", *lines[1:])["finished"])
        self.assertIn("NO FIX CYCLE", self.run_script("check_gate.py", "--fix-cycle").stdout)

    def test_a_fix_committed_before_the_cycle_opened_is_its_fix_commit(self) -> None:
        """r20-t3-haiku-6: the builder fixed the reviewer's crash as its own commit before any cycle existed."""
        self.vetoed()
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text(skill.read_text() + FIXED)
        self.git("add", "-A", "output")
        self.git("commit", "-q", "-m", "fix the veto")
        self.relog(self.git("rev-parse", "HEAD"), 0.95)
        self.assertIn("5.4 fix cycle 1 of 2", self.next(), "not the post-panel UNSHIPPED")
        self.open()
        record = json.loads((self.ws / "work/experiments/panel/fix-cycles/1/cycle.json").read_text())
        self.assertEqual(record["base_commit"], self.commit, "the base is the reviewed skill's commit")
        self.assertIn("declares no `lines:` for verifier-b.veto", self.next())
        self.notes(1, {"verifier-b.veto": "SKILL.md:6"})
        self.assertIn("re-run the gate on the fixed skill", self.next())

    def test_a_fix_that_cannot_be_scored_ends_unshipped(self) -> None:
        self.vetoed()
        self.open()
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text(skill.read_text() + FIXED)
        self.notes(1, {"verifier-b.veto": "SKILL.md:6"})
        self.git("add", "-A", "output")
        self.git("commit", "-q", "-m", "fix cycle 1")
        with open(self.ws / "work" / "evaluation" / "gate-attempts.jsonl", "a") as fh:
            fh.write(json.dumps({"step": "rescore", "error": "evaluate.sh printed no METRIC line",
                                 "commit": self.git("rev-parse", "HEAD")}) + "\n")
        nxt = self.next()
        self.assertIn("`Verdict: UNSHIPPED` (fix cycle 1: the fixed skill cannot be scored", nxt)
        self.assertNotIn("re-check", nxt)

    def test_no_cycle_when_fixing_cannot_ship_or_nothing_is_cited(self) -> None:
        self.log(0.70)  # the metric already allows only UNSHIPPED
        self.gate()
        self.panel(HIGH, **{"verifier-b": {"veto": {"kind": "safety", **CITED}}})
        self.assertNotIn("fix cycle", self.next())
        self.assertIn("NO FIX CYCLE", self.run_script("check_gate.py", "--fix-cycle").stdout)

    def test_an_ordinary_post_panel_edit_still_cannot_regate(self) -> None:
        self.log(0.95)
        self.gate()
        self.panel(HIGH)
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Edited.\n---\n# Demo\n")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3)
        self.assertIn("the panel already ran its 1 round(s)", out.stderr)
        self.assertIn("NO FIX CYCLE", self.run_script("check_gate.py", "--fix-cycle").stdout)


class PanelPathFixCycleTest(FixCycleFixture):
    """The panel-verdict path (`metric: llm-judge`): a terminal ITERATE or ESCALATE on cited failure scenarios."""

    def setUp(self) -> None:
        super().setUp()
        self.rubric("llm-judge")

    def test_full_profile_escalate_with_a_cited_scenario_caps_at_caveats(self) -> None:
        self.log(0.95)
        self.gate()
        self.score("verifier-a", (6, 6))
        self.score("verifier-b", (6, 6), [{"scenario": "uncited doubt", "severity": "low"}])
        self.score("devils-advocate", (6, 6), [SCENARIO], escalation="ESCALATE: item text runs as shell")
        self.assertIn("PANEL CONSENSUS: ESCALATE", self.run_script("check_gate.py", "--consensus").stdout)
        offer = self.next()
        self.assertIn("devils-advocate.F1 (finding)", offer)
        self.assertNotIn("verifier-b.F", offer, "a scenario without evidence and repro is not a finding")
        self.open()
        self.fix(findings=("devils-advocate.F1",))
        self.gate()
        self.assertIn("one subagent per role (devils-advocate)", self.next())
        self.recheck("devils-advocate", {"devils-advocate.F1": self.resolved()})
        head, lines = self.verdict_lines()
        self.assertIn("`Verdict: SHIP WITH CAVEATS`", head)
        self.assertTrue(lines[-1].startswith("SHIP RULE: SHIP WITH CAVEATS (fix cycle 1; panel caps at SHIP WITH "
                                             "CAVEATS)"), lines[-1])
        self.assertIn("allows at most SHIP WITH CAVEATS", self.finish("SHIP", *lines[1:])["reason"])
        self.assertTrue(self.finish("SHIP WITH CAVEATS", *lines[1:])["finished"])
        self.assert_edit_ends_unshipped()

    def assert_edit_ends_unshipped(self) -> None:
        """A3 after a closed cycle, on any path: an edit can't re-gate or start a panel round; UNSHIPPED."""
        (self.ws / "BENCHMARK.md").unlink()
        skill = self.ws / "output" / "demo" / "SKILL.md"
        skill.write_text(skill.read_text() + "More.\n")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("`Verdict: UNSHIPPED`", self.next())

    def test_an_iterate_with_phase_4_left_returns_to_phase_4_not_a_cycle(self) -> None:
        self.log(0.95)
        self.gate()
        for role in HIGH:
            self.score(role, (5, 5), [SCENARIO])
        self.run_script("check_gate.py", "--consensus")
        nxt = self.next()
        self.assertNotIn("fix cycle", nxt)
        self.assertIn("LOOP OPEN", nxt)

    def test_an_uncited_escalate_ends_unshipped_as_before(self) -> None:
        self.log(0.95)
        self.gate()
        for role in HIGH:
            self.score(role, (6, 6), escalation="ESCALATE: risky" if role == "devils-advocate" else None)
        self.run_script("check_gate.py", "--consensus")
        self.assertIn("`Verdict: UNSHIPPED`", self.next())


class LiteFixCycleTest(FixCycleFixture):
    env = {"CSA_PROFILE": "lite"}

    def test_lite_terminal_iterate_on_a_cited_scenario(self) -> None:
        """r20-t1-haiku-1/-2: a lite reviewer's ITERATE with the Phase 4 budget spent."""
        self.rubric("llm-judge")
        spent = [{"experiment": i, "commit": self.commit, "metric": 0.95, "held_out": 0.95, "status": "discard"}
                 for i in (1, 2, 3)]
        self.log(0.95, *spent)
        self.gate()
        self.score("reviewer", (5, 5), [SCENARIO])
        self.assertIn("PANEL CONSENSUS: ITERATE", self.run_script("check_gate.py", "--consensus").stdout)
        self.assertIn("reviewer.F1 (finding)", self.next())
        self.open()
        self.fix(findings=("reviewer.F1",))
        self.gate()
        self.assertIn("one subagent per role (reviewer)", self.next())
        self.recheck("reviewer", {"reviewer.F1": self.resolved()})
        head, lines = self.verdict_lines()
        self.assertIn("`Verdict: SHIP WITH CAVEATS`", head)
        self.assertTrue(self.finish("SHIP WITH CAVEATS", *lines[1:])["finished"])
        PanelPathFixCycleTest.assert_edit_ends_unshipped(self)

    def test_lite_metric_first_veto_unresolved_ends_unshipped_at_the_cap(self) -> None:
        self.log(0.95)
        self.gate()
        self.score("reviewer", (9, 9), escalation="ESCALATE: crashes", veto={"kind": "escalate", **CITED})
        self.run_script("check_gate.py", "--consensus")
        for n in (1, 2):
            self.open(n)
            self.fix(n, body=f"Fix {n}.\n", spans=f"SKILL.md:{5 + n}", findings=("reviewer.veto",))
            self.gate()
            out = self.recheck("reviewer", {"reviewer.veto": {"resolved": False, "evidence": None,
                                                              "regression": None}})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        head, lines = self.verdict_lines()
        self.assertIn("`Verdict: UNSHIPPED`", head)
        self.assertIn("FIX CYCLE 2 of 2: resolved none; unresolved reviewer.veto", lines)


if __name__ == "__main__":
    unittest.main()
