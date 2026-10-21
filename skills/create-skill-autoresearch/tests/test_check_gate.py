"""Offline tests for check_gate.py: the gate check, --finish and the fast path.

Fixtures (fake `claude`, fake adapters, temp workspaces) are in helpers.py. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import locale
import subprocess
import unittest

from helpers import TempWorkspace, WINDOWS, _until
import gate_common  # noqa: E402  (on sys.path through helpers)


class FinishTest(TempWorkspace):
    def benchmark(self, text: str) -> None:
        (self.ws / "BENCHMARK.md").write_text(text)

    def finish(self) -> dict:
        out = self.run_script("check_gate.py", "--finish", "--json")
        data = json.loads(out.stdout)
        self.assertEqual(out.returncode, 0 if data["finished"] else 1)
        return data

    def gate_line(self) -> str:
        return self.run_script("check_gate.py").stdout.strip()

    def test_missing_benchmark_is_unfinished(self) -> None:
        self.make_build(["gold-01"])
        self.assertIn("no BENCHMARK.md at the build root", self.finish()["reason"])
        self.assertIn("BUILD UNFINISHED", self.run_script("check_gate.py", "--finish").stdout)

    def test_benchmark_without_a_verdict_line_is_unfinished(self) -> None:
        self.make_build(["gold-01"])
        self.benchmark("# Benchmark\n\nScores: 0.9. The panel liked it; we SHIP soon.\n")
        self.assertIn("no `Verdict: <label>` line", self.finish()["reason"])

    def test_iterate_is_not_terminal(self) -> None:
        self.make_build(["gold-01"])
        self.benchmark("## Verdict: ITERATE\n")
        data = self.finish()
        self.assertEqual((data["finished"], data["verdict"]), (False, "ITERATE"))
        self.assertIn("write UNSHIPPED", data["reason"])

    def test_unshipped_without_a_gate_attempt_is_unfinished(self) -> None:
        self.make_build(["gold-01"])
        self.benchmark("# Benchmark\n\n**Verdict — UNSHIPPED** (budget exhausted; a draft, not published)\n")
        data = self.finish()
        self.assertFalse(data["finished"], "the cycle-3 task-2 Haiku 3 shape: UNSHIPPED, gate never run")
        self.assertIn("UNSHIPPED needs a gate attempt", data["reason"])
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        self.assertIn("needs a gate attempt", self.finish()["reason"], "a record alone is not a gate attempt")

    def test_unshipped_after_a_failed_record_alone_is_unfinished(self) -> None:
        self.make_build(["gold-01"])
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-01" / "INVALID").write_text("")
        self.assertEqual(self.run_script("gate.py", "record").returncode, 3)
        self.assertEqual([a["step"] for a in gate_common.read_attempts(self.ws)], ["record"])
        self.benchmark("Verdict: UNSHIPPED\n")
        self.assertIn("needs a gate attempt", self.finish()["reason"],
                      "a failed 3.6 record is not a 5.0 gate attempt (round-3 adversary finding 7)")

    @unittest.skipUnless(WINDOWS, "the fallback is the locale's code page, which is UTF-8 here")
    def test_benchmark_in_the_windows_code_page_is_read(self) -> None:
        self.make_build(["gold-01"])
        encoding = locale.getpreferredencoding(False)
        (self.ws / "BENCHMARK.md").write_bytes("**Verdict \u2014 ITERATE**\n".encode(encoding))
        self.assertEqual(self.finish()["verdict"], "ITERATE", "the code page's em dash still separates the label")

    def test_unshipped_finishes_after_a_gate_that_failed_to_start(self) -> None:
        self.make_build(["gold-01"])
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-01" / "INVALID").write_text("")
        self.assertEqual(self.run_script("gate.py", "--detach", "--model", "m").returncode, 3)
        self.benchmark("Verdict: UNSHIPPED\n")
        self.assertEqual(self.finish(), {"finished": True, "verdict": "UNSHIPPED", "reason": "ok"})
        attempts = gate_common.read_attempts(self.ws)
        self.assertEqual([a["step"] for a in attempts], ["gate"])
        self.assertIn("fail to load", attempts[0]["error"])

    def test_unshipped_finishes_after_a_gate_run_with_an_invalid_artifact(self) -> None:
        self.make_build(["gold-01"])
        self.assertEqual(self.run_script("gate.py", "--model", "m", FAKE_MODE="partial").returncode, 1)
        self.benchmark("Verdict: UNSHIPPED\n")
        self.assertTrue(self.finish()["finished"])

    def gated(self, cases: list[str]) -> None:
        self.make_build(cases)
        self.write_rubric()
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)

    SHIP_PANEL = {"verifier-a": (9, 9), "verifier-b": (9, 8), "devils-advocate": (8, 8)}

    def test_ship_needs_a_valid_gate_and_its_line(self) -> None:
        self.gated(["gold-01", "trigger-1"])
        self.benchmark("**Verdict: SHIP_WITH_CAVEATS**\n")
        self.assertIn("lacks check_gate.py's current GATE VALID line", self.finish()["reason"])
        stale = self.gate_line().replace("mean gold delta=+0.500", "mean gold delta=+0.900")
        self.assertNotEqual(stale, self.gate_line())
        self.benchmark(f"**Verdict: SHIP_WITH_CAVEATS**\n\n{stale}\n")
        data = self.finish()
        self.assertFalse(data["finished"], "a GATE VALID line from another gate run is not this gate's")
        self.assertIn(self.gate_line(), data["reason"], "the reason quotes the line to copy")
        self.benchmark(f"**Verdict: SHIP_WITH_CAVEATS**\n\n{self.gate_line()}\n")
        self.assertEqual(self.finish()["verdict"], "SHIP WITH CAVEATS")
        self.assertIn("no current role receipt from verifier-a, verifier-b, devils-advocate", self.finish()["reason"])
        line = self.run_panel(self.SHIP_PANEL)
        self.assertIn("lacks the panel's current PANEL CONSENSUS line", self.finish()["reason"])
        self.benchmark(f"**Verdict: SHIP_WITH_CAVEATS**\n\n{self.gate_line()}\n{line}\n")
        self.assertTrue(self.finish()["finished"])
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Edited.\n---\n")
        data = self.finish()
        self.assertFalse(data["finished"])
        self.assertIn("skill changed since the gate", data["reason"])

    def test_receipts_from_before_a_skill_change_never_finish_a_ship(self) -> None:
        self.gated(["gold-01", "trigger-1"])
        self.run_panel(self.SHIP_PANEL)
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Fixed after the panel.\n---\n")
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0, "the re-run gate validates")
        self.assertEqual(self.run_script("check_gate.py", "--panel").returncode, 0, "and --panel passes it")
        self.benchmark(f"Verdict: SHIP WITH CAVEATS\n\n{self.gate_line()}\n")
        data = self.finish()
        self.assertFalse(data["finished"], "the s-t2-factory-v030-haiku-2 shape: no verifier re-scored the fix")
        self.assertIn("no current role receipt from verifier-a, verifier-b, devils-advocate", data["reason"])
        consensus = self.run_script("check_gate.py", "--consensus")
        self.assertEqual(consensus.returncode, 1)
        self.assertIn("PANEL INCOMPLETE", consensus.stdout)
        line = self.run_panel(self.SHIP_PANEL)
        self.benchmark(f"Verdict: SHIP WITH CAVEATS\n\n{self.gate_line()}\n{line}\n")
        self.assertTrue(self.finish()["finished"])

    def test_a_verdict_above_the_consensus_is_unfinished(self) -> None:
        self.gated(["gold-01", "trigger-1"])
        line = self.run_panel({"verifier-a": (8, 8), "verifier-b": (8, 8), "devils-advocate": (8, 8)})
        self.assertIn("PANEL CONSENSUS: SHIP WITH CAVEATS (allows SHIP WITH CAVEATS); overall=0.800", line)
        self.benchmark(f"Verdict: SHIP\n\n{self.gate_line()}\n{line}\n")
        self.assertIn("the panel's consensus allows at most SHIP WITH CAVEATS", self.finish()["reason"])
        self.benchmark(f"Verdict: SHIP WITH CAVEATS\n\n{self.gate_line()}\n{line}\n")
        self.assertTrue(self.finish()["finished"])

    def test_iterate_block_and_escalate_allow_only_unshipped(self) -> None:
        self.gated(["gold-01", "trigger-1"])
        for scores, escalation, label in (
                ({"verifier-a": (5, 5), "verifier-b": (5, 6), "devils-advocate": (5, 5)}, None, "ITERATE"),
                ({"verifier-a": (2, 9), "verifier-b": (2, 9), "devils-advocate": (2, 8)}, None, "BLOCK"),
                (self.SHIP_PANEL, "ESCALATE: the guard misses X", "ESCALATE")):
            line = self.run_panel(scores, escalation)
            self.assertIn(f"PANEL CONSENSUS: {label} (allows UNSHIPPED)", line)
            self.benchmark(f"Verdict: SHIP WITH CAVEATS\n\n{self.gate_line()}\n{line}\n")
            self.assertIn("allows at most UNSHIPPED", self.finish()["reason"], label)
            self.benchmark(f"Verdict: UNSHIPPED\n\n{self.gate_line()}\n")
            self.assertIn("lacks the panel's current PANEL CONSENSUS line", self.finish()["reason"],
                          "UNSHIPPED states why beside the label")
            self.benchmark(f"Verdict: UNSHIPPED\n\n{self.gate_line()}\n{line}\n")
            self.assertTrue(self.finish()["finished"], label)

    def test_unshipped_on_a_valid_gate_carries_its_line(self) -> None:
        self.gated(["gold-01", "trigger-1"])
        self.benchmark("Verdict: UNSHIPPED\n")
        self.assertIn("lacks check_gate.py's current GATE VALID line beside the UNSHIPPED", self.finish()["reason"])
        self.benchmark(f"Verdict: UNSHIPPED\n\n{self.gate_line()}\n")
        self.assertTrue(self.finish()["finished"], "no panel yet: the gate line is the reason on record")

    def test_a_hand_edited_consensus_is_stale(self) -> None:
        self.gated(["gold-01", "trigger-1"])
        line = self.run_panel({"verifier-a": (8, 8), "verifier-b": (8, 8), "devils-advocate": (8, 8)})
        path = self.ws / "work" / "experiments" / "panel" / "consensus.json"
        data = json.loads(path.read_text())
        data["allowed"] = "SHIP"
        data["line"] = line.replace("allows SHIP WITH CAVEATS", "allows SHIP")
        path.write_text(json.dumps(data))
        self.benchmark(f"Verdict: SHIP\n\n{self.gate_line()}\n{data['line']}\n")
        self.assertIn("consensus.json is stale", self.finish()["reason"])
        self.receipt("verifier-a", 9, 9)
        self.assertIn("is stale", self.finish()["reason"], "a re-scored receipt needs a fresh --consensus")

    def test_plain_ship_with_a_gold_delta_at_or_below_zero_is_unfinished(self) -> None:
        self.gated(["gold-01", "trigout-2"])
        line = self.run_panel(self.SHIP_PANEL)
        self.assertIn("PANEL CONSENSUS: SHIP (allows SHIP)", line)
        self.benchmark(f"Verdict: SHIP\n\n{self.gate_line()}\n{line}\n")
        self.assertIn("gold delta<=0 on trigout-2 caps the verdict at SHIP WITH CAVEATS", self.finish()["reason"])
        self.benchmark(f"Verdict: SHIP WITH CAVEATS\n\n{self.gate_line()}\n{line}\n")
        self.assertTrue(self.finish()["finished"])
        self.assertEqual(self.run_script("check_gate.py", "--panel", "--finish").returncode, 2)

    def test_one_run_gate_never_finishes_a_ship(self) -> None:
        self.make_build(["gold-01"])
        self.assertEqual(self.run_script("gate.py", "--model", "m", "--runs", "1").returncode, 1)
        self.assertIn("1 completed runs; 3 required", self.gate_line())
        self.benchmark("Verdict: SHIP\n\nGATE VALID: mode=plugin artifact=work/evaluation/plugin-eval.json cases=1\n")
        self.assertIn("SHIP needs a valid gate", self.finish()["reason"])
        for args in (["--min-runs", "1"], ["--finish", "--min-runs", "1"]):
            self.assertEqual(self.run_script("check_gate.py", *args).returncode, 2, "no flag lowers the 3 runs")

    def test_live_gate_is_unfinished(self) -> None:
        self.make_build(["gold-01"])
        self.benchmark("Verdict: UNSHIPPED\n")
        out = self.run_script("gate.py", "--detach", "--model", "m", FAKE_MODE="hang")
        self.assertEqual(out.returncode, 0, out.stderr)
        try:
            self.assertTrue(_until(lambda: (self.ws / "work" / "pids" / "gate-plugin-eval.json").is_file()))
            self.assertIn("still running", self.finish()["reason"])
        finally:
            self.assertEqual(self.run_script("gate.py", "--stop").returncode, 0)
        self.assertTrue(self.finish()["finished"])


class FastPathTest(TempWorkspace):
    def fast_path(self) -> str:
        out = self.run_script("check_gate.py", "--fast-path")
        self.assertEqual(out.returncode, 0 if out.stdout.startswith("FAST PATH:") else 1, out.stdout + out.stderr)
        return out.stdout.strip()

    def log(self, *experiments: dict, where: str = "work/experiments") -> None:
        path = self.ws / where / "autoresearch.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in experiments))

    def build(self, cases: list[str]) -> None:
        self.make_build(cases)
        self.write_rubric(target=0.85)

    def test_a_baseline_at_target_with_uplift_everywhere_skips_phase_4(self) -> None:
        self.build(["gold-01", "trigger-1"])
        self.assertIn("NO FAST PATH: the gate does not validate", self.fast_path())
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        self.assertIn("NO FAST PATH: no experiment 0 in autoresearch.jsonl", self.fast_path())
        self.log({"experiment": 0, "commit": "", "metric": 0.84, "status": "baseline"})
        self.assertEqual(self.fast_path(), "NO FAST PATH: baseline overall_score 0.840 is below target_score 0.85")
        self.log({"type": "validation_check", "experiment": 3}, {"experiment": 0, "metric": 0.85}, where=".")
        self.assertEqual(self.fast_path(), "FAST PATH: baseline overall_score 0.850 >= target_score 0.85 and every "
                                           "gold delta > 0 (mean +0.500); skip the Phase 4 loop (4.1-4.6) and "
                                           "continue at 5.1", "the root log wins; a validation check is no experiment")
        self.log({"experiment": 0, "metric": 0.9}, {"experiment": 1, "metric": 0.92}, where=".")
        self.assertIn("NO FAST PATH: Phase 4 already logged 1 experiment(s)", self.fast_path())

    def test_a_gold_delta_at_or_below_zero_needs_phase_4(self) -> None:
        self.build(["gold-01", "trigout-2"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        self.log({"experiment": 0, "metric": 0.95})
        self.assertEqual(self.fast_path(), "NO FAST PATH: gold delta<=0 on trigout-2")

    def test_a_skill_changed_since_the_baseline_commit_needs_phase_4(self) -> None:
        self.build(["gold-01"])
        git = ["git", "-C", str(self.ws), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "add", "output"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "baseline"], check=True)
        commit = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        self.log({"experiment": 0, "commit": commit, "metric": 0.9})
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        self.assertTrue(self.fast_path().startswith("FAST PATH:"))
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Edited.\n---\n")
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        self.assertEqual(self.fast_path(), f"NO FAST PATH: output/demo/ changed since experiment 0 (commit {commit})")


class CheckGateTest(TempWorkspace):
    def record_gate(self, cases: list[str], finished: bool = True, hashed: bool = True) -> None:
        record = {"mode": "manual", "skill": "demo", "cases": cases, "artifact": "work/evaluation/uplift.json",
                  "started": "2026-10-17T00:00:00+00:00", "finished": "2026-10-17T00:01:00+00:00" if finished else None}
        if hashed:
            evaluation = self.ws / "work" / "evaluation"
            record |= {"skill_sha256": gate_common.skill_sha256(self.ws, "demo"),
                       "cases_sha256": gate_common.cases_sha256(self.ws, cases),
                       "split_sha256": gate_common.split_sha256(gate_common.read_data_split(evaluation / "data-split.yaml")),
                       "run_case_sha256": gate_common.file_sha256(evaluation / "run-case.sh"),
                       "inputs_sha256": gate_common.inputs_sha256(self.ws),
                       "artifact_sha256": gate_common.file_sha256(evaluation / "uplift.json")}
        (self.ws / "work" / "evaluation" / "gate.json").write_text(json.dumps(record))

    def write_uplift(self, cases: list[str], runs: int = 3, where: str = "work/evaluation", **extra: object) -> None:
        path = self.ws / where / "uplift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"cases": [{"case": c, "with": [1.0] * runs, "without": [0.0] * runs} for c in cases],
                "partial": False}
        path.write_text(json.dumps(data | extra))

    def gated(self, cases: list[str], **uplift: object) -> None:
        self.make_build(cases, invoked=False)
        self.write_uplift(cases, **uplift)
        self.record_gate(cases)

    def verdict(self) -> dict:
        return json.loads(self.run_script("check_gate.py", "--json").stdout)

    def test_valid_manual_gate(self) -> None:
        self.gated(["input-01"])
        out = self.run_script("check_gate.py")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("mode=manual", out.stdout)
        self.assertIn("mean gold delta=+1.000", out.stdout)

    def test_manual_artifact_without_a_gate_record_is_rejected(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_uplift(["input-01"])
        out = self.run_script("check_gate.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("not written by a gate run", out.stdout)

    def test_unfinished_gate_is_rejected(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.record_gate(["input-01"], finished=False)
        self.write_uplift(["input-01"])
        self.assertEqual(self.verdict()["code"], "gate_unfinished")

    def test_unhashed_gate_record_is_rejected(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.record_gate(["input-01"], hashed=False)
        self.write_uplift(["input-01"])
        self.assertEqual(self.verdict()["code"], "gate_unhashed")

    def test_partial_flag_must_be_present_and_false(self) -> None:
        self.make_build(["input-01"], invoked=False)
        path = self.ws / "work" / "evaluation" / "uplift.json"
        path.write_text(json.dumps({"cases": [{"case": "input-01", "with": [1.0] * 3, "without": [0.0] * 3}]}))
        self.record_gate(["input-01"])
        self.assertIn('records no "partial": false', self.run_script("check_gate.py").stdout)

    def test_missing_artifact(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.record_gate(["input-01"])
        out = self.run_script("check_gate.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("mode manual: work/evaluation/uplift.json not found", out.stdout)

    def test_wrong_path_is_missing(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.record_gate(["input-01"])
        self.write_uplift(["input-01"], where="benchmarks/demo")
        self.assertIn("uplift.json not found", self.run_script("check_gate.py").stdout)

    def test_too_few_runs(self) -> None:
        self.gated(["input-01"], runs=2)
        self.assertIn("has 2 runs; 3 required", self.run_script("check_gate.py").stdout)

    def test_case_coverage_must_match_exactly(self) -> None:
        self.make_build(["input-01", "input-02"], invoked=False)
        self.write_uplift(["input-01", "input-03"])
        self.record_gate(["input-01", "input-02"])
        out = self.run_script("check_gate.py").stdout
        self.assertIn("missing ['input-02']", out)
        self.assertIn("unrecorded ['input-03']", out)

    def test_no_recorded_cases(self) -> None:
        self.make_build([], invoked=False)
        (self.ws / "work" / "evaluation" / "data-split.yaml").write_text("plugin_eval: []\n")
        self.assertIn("records no gate cases", self.run_script("check_gate.py").stdout)

    def test_non_positive_delta_is_reported(self) -> None:
        self.make_build(["input-01"], invoked=False)
        path = self.ws / "work" / "evaluation" / "uplift.json"
        path.write_text(json.dumps({"cases": [{"case": "input-01", "with": [0.5] * 3, "without": [0.5] * 3}],
                                    "partial": False}))
        self.record_gate(["input-01"])
        self.assertEqual(self.verdict()["non_positive"], ["input-01"])


if __name__ == "__main__":
    unittest.main()
