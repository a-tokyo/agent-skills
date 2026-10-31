"""Offline tests for gate.py: record, the plugin-eval and manual gates, the detached lifecycle.

Fixtures (fake `claude`, fake adapters, temp workspaces) are in helpers.py. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import DETACHED, SCORING_ADAPTER, SCRIPTS, TempWorkspace, _env, _group_alive, _script, _until
import gate  # noqa: E402  (on sys.path through helpers)
import gate_common  # noqa: E402  (on sys.path through helpers)


class GatePluginTest(TempWorkspace):
    CASES = ["gold-01", "gold-02", "near-miss", "trigger-1"]

    def test_happy_path_writes_a_valid_artifact(self) -> None:
        self.make_build(self.CASES)
        stale = self.ws / "work" / "evaluation" / "plugin" / "skills" / "demo" / "stale.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("old copy")
        out = self.run_script("gate.py", "--model", "claude-haiku-5-5")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("GATE VALID: mode=plugin", out.stdout)
        data = self.evaluation("plugin-eval.json")
        self.assertEqual(data["skillCopy"], ["SKILL.md"], "skill copy refreshed from output/")
        argv = [json.loads(line) for line in self.argv_log.read_text().splitlines()][-1]
        for flag in ("--ablation", "with-without", "--scaffold", "--trust-plugin", "--no-publish", "--threshold"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-haiku-5-5")
        self.assertEqual(argv[argv.index("-j") + 1], "8", "plugin eval's concurrency cap by default")
        record = self.evaluation("gate.json")
        self.assertEqual(record["mode"], "plugin")
        self.assertEqual(record["skill_sha256"], gate_common.skill_sha256(self.ws, "demo"))
        self.assertEqual(record["cases_sha256"], gate_common.cases_sha256(self.ws, self.CASES))
        self.assertEqual(self.record("gate")["cmd"][2], str(SCRIPTS / "gate.py"), "the gate ran as the owned job")
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)

    def test_wait_defaults_fit_one_claude_code_shell_call(self) -> None:
        import gate  # noqa: E402  (on sys.path through helpers)

        self.assertEqual((gate.WAIT_SECONDS, gate.DEFAULT_JOBS), (570, 8))
        self.assertLess(gate.WAIT_SECONDS + 15, 600, "returns inside the 600 s a Claude Code Bash call may wait")

    def test_summary_separates_gold_cases_from_indicators(self) -> None:
        self.make_build(self.CASES)
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual((verdict["cases"], verdict["gold_cases"], verdict["mean_delta"]), (4, 2, 0.5))
        self.assertEqual(verdict["non_positive"], [], "delta 0 on a trigger or near miss is not a gold failure")
        self.assertEqual(verdict["triggers"], {"trigger-1": "3/3"})
        self.assertEqual(verdict["near_misses"], {"near-miss": "6/6"})
        line = self.run_script("check_gate.py").stdout
        self.assertIn("gold=2 mean gold delta=+0.500; gold delta<=0: none; "
                      "trigger fired (with arm): trigger-1 3/3; near-miss quiet: near-miss 6/6", line)

    def test_a_trigger_that_misses_one_with_arm_run_invalidates_the_gate(self) -> None:
        self.make_build(self.CASES)
        out = self.run_script("gate.py", "--model", "m", FAKE_TRIGGER_MISSES="1")
        self.assertEqual(out.returncode, 1, out.stdout)
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "trigger_failed")
        self.assertIn("case 'trigger-1': the skill did not fire in 1/3 with-arm runs", verdict["reason"])
        self.assertIn("return to 4.6", verdict["reason"])

    def test_a_near_miss_that_fires_in_the_bare_arm_invalidates_the_gate(self) -> None:
        self.make_build(self.CASES)
        self.assertEqual(self.run_script("gate.py", "--model", "m", FAKE_NEAR_MISS_FIRES="1").returncode, 1)
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "near_miss_fired")
        self.assertIn("near miss 'near-miss' fired the skill in 1/6 runs (either arm)", verdict["reason"])

    def test_plugin_mode_warns_on_a_model_that_is_not_claude(self) -> None:
        self.make_build(self.CASES)
        out = self.run_script("gate.py", "--model", "gpt-5.6")
        self.assertIn("--model 'gpt-5.6' names none; a build on another host", out.stdout + out.stderr)
        out = self.run_script("gate.py", "--model", "claude-haiku-5-5")
        self.assertNotIn("names none", out.stdout + out.stderr)

    def test_worked_case_with_a_skill_indicator_stays_gold(self) -> None:
        cases = ["holdout-03", "near-miss", "trigger-1"]
        self.make_build(cases, training='["input/holdout-03.md"]')
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertTrue(verdict["valid"], verdict["reason"])
        self.assertEqual((verdict["gold_cases"], verdict["mean_delta"]), (1, 0.4),
                         "approval.md is score-bearing, so skill-fired does not make holdout-03 an indicator")
        self.assertEqual(verdict["triggers"], {"holdout-03": "3/3", "trigger-1": "3/3"},
                         "its skill-fired indicator is still reported")

    def test_should_trigger_case_that_grades_output_is_gold_and_keeps_its_indicator(self) -> None:
        self.make_build(["gold-01", "trigout-2", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual((verdict["cases"], verdict["gold_cases"]), (3, 2))
        self.assertEqual(verdict["non_positive"], ["trigout-2"], "its delta 0 counts like any gold case")
        self.assertEqual(verdict["triggers"], {"trigout-2": "3/3", "trigger-1": "3/3"})
        self.assertEqual(verdict["mean_delta"], 0.25)

    def test_indicators_only_fail_when_gold_standards_are_recorded(self) -> None:
        self.make_build(["near-miss", "trigger-1"], training="\n  - input/gold-01.md")
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 1)
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "no_gold")

    def test_indicators_only_pass_without_recorded_gold_standards(self) -> None:
        self.make_build(["near-miss", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)

    def test_gold_case_needs_a_numeric_delta(self) -> None:
        self.make_build(["holdout-03", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "--model", "m", FAKE_MODE="nodelta").returncode, 1)
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "no_delta")
        self.assertIn("'holdout-03'", verdict["reason"])

    def test_skill_change_after_the_gate_is_stale(self) -> None:
        self.make_build(self.CASES)
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        cache = self.ws / "output" / "demo" / "scripts" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "x.pyc").write_bytes(b"\0")
        self.assertEqual(self.run_script("check_gate.py").returncode, 0, "interpreter caches are not a change")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Edited.\n---\n")
        out = self.run_script("check_gate.py", "--json")
        self.assertEqual(out.returncode, 1)
        verdict = json.loads(out.stdout)
        self.assertEqual(verdict["code"], "stale_skill")
        self.assertTrue(verdict["reason"].startswith("skill changed since the gate; re-run 5.0"), verdict["reason"])

    def test_case_change_after_the_gate_is_stale(self) -> None:
        self.make_build(self.CASES)
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-01" / "prompt.md").write_text("Easier.\n")
        out = self.run_script("check_gate.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("gate cases changed since the gate", out.stdout)

    def test_partial_artifact_is_rejected(self) -> None:
        self.make_build(self.CASES)
        out = self.run_script("gate.py", "--model", "m", FAKE_MODE="partial")
        self.assertEqual(out.returncode, 1)
        check = self.run_script("check_gate.py")
        self.assertEqual(check.returncode, 1)
        self.assertIn("partial (interrupted)", check.stdout)

    def test_too_few_runs_are_rejected(self) -> None:
        self.make_build(self.CASES)
        out = self.run_script("gate.py", "--model", "m", FAKE_MODE="fewruns")
        self.assertEqual(out.returncode, 1)
        self.assertIn("2 completed runs; 3 required", out.stdout)

    def test_timeout_stops_the_eval_and_fails(self) -> None:
        self.make_build(self.CASES)
        out = self.run_script("gate.py", "--model", "m", "--timeout", "1", FAKE_MODE="hang")
        self.assertEqual(out.returncode, 1)
        self.assertIn("timed out", out.stdout)
        record = self.record("gate-plugin-eval")
        self.assertIsNotNone(record["stopped"])
        self.assertFalse(_group_alive(record["pgid"]))

    def test_stale_artifact_never_passes(self) -> None:
        self.make_build(self.CASES)
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        out = self.run_script("gate.py", "--model", "m", FAKE_MODE="silent")
        self.assertEqual(out.returncode, 1)
        self.assertIn("plugin-eval.json not found", out.stdout)
        self.assertTrue((self.ws / "work" / "evaluation" / "plugin-eval.prev.json").is_file())

    def test_hand_edited_case_list_is_replaced_by_the_suite_on_disk(self) -> None:
        self.make_build(self.CASES)
        extra = self.ws / "work" / "evaluation" / "plugin" / "evals" / "extra"
        extra.mkdir()
        (extra / "prompt.md").write_text("x")
        split = self.ws / "work" / "evaluation" / "data-split.yaml"
        split.write_text(split.read_text().replace('  - "gold-02"\n', ""))
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        recorded = gate_common.read_data_split(split).plugin_eval
        self.assertEqual(recorded, ["extra", "gold-01", "gold-02", "near-miss", "trigger-1"])
        self.assertEqual(json.loads(self.run_script("check_gate.py", "--json").stdout)["cases"], 5)

    def victim(self, name: str = "victim") -> Path:
        """A SKILL-shaped directory in the temp root, outside the workspace: what --skill must never reach."""
        victim = self.root / "elsewhere" / name
        victim.mkdir(parents=True)
        (victim / "SKILL.md").write_text("---\nname: victim\ndescription: Not the build's.\n---\n")
        return victim

    def test_skill_that_is_a_path_is_refused_and_nothing_outside_is_touched(self) -> None:
        self.make_build(self.CASES)
        victim = self.victim()
        for skill in (str(victim), "../../elsewhere/victim", f"..{os.sep}victim", "demo/../demo", "demo/x", "Demo", ""):
            with self.subTest(skill=skill):
                for script, args in (("gate.py", ["--model", "m"]), ("gate.py", ["record"]), ("check_gate.py", [])):
                    out = self.run_script(script, *args, "--skill", skill)
                    self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
                    self.assertIn("is not a skill name", out.stdout + out.stderr)
                self.assertTrue((victim / "SKILL.md").is_file())
        self.assertFalse(self.argv_log.exists() and "with-without" in self.argv_log.read_text(), "no eval ran")

    def test_symlinked_skill_copy_is_refused_before_anything_is_deleted(self) -> None:
        self.make_build(self.CASES)
        victim = self.victim("demo")
        try:
            (self.ws / "work" / "evaluation" / "plugin" / "skills").symlink_to(victim.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"no symlinks here: {exc}")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("refuses a symlink", out.stdout)
        self.assertTrue((victim / "SKILL.md").is_file())

    def test_unloadable_case_stops_the_gate_before_it_runs(self) -> None:
        self.make_build(self.CASES)
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-02" / "INVALID").write_text("")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3)
        self.assertIn("1 case file(s) fail to load", out.stdout)
        self.assertIn(f"{Path('gold-02', 'case.yaml')}: invalid case.yaml", out.stdout)
        self.assertNotIn("--ablation\", \"with-without", self.argv_log.read_text(), "the eval never ran")


class RecordTest(TempWorkspace):
    def split(self) -> gate_common.DataSplit:
        return gate_common.read_data_split(self.ws / "work" / "evaluation" / "data-split.yaml")

    def test_fresh_suite_is_recorded_with_the_probe_exit_code(self) -> None:
        self.make_build(["gold-01", "trigger-1"])
        path = self.ws / "work" / "evaluation" / "data-split.yaml"
        path.write_text("# split notes\nstrategy: leave-one-out\ntraining: []\n")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("recorded 2 gate cases for mode plugin", out.stdout)
        self.assertNotIn("gold-01", out.stdout, "the case list is --verbose detail")
        verbose = self.run_script("gate.py", "record", "--verbose")
        self.assertIn("data-split.yaml: gold-01, trigger-1", verbose.stdout)
        split = self.split()
        self.assertEqual((split.plugin_eval, split.plugin_eval_available), (["gold-01", "trigger-1"], 0))
        self.assertTrue(path.read_text().startswith("# split notes\nstrategy: leave-one-out\ntraining: []\n"))
        load = [json.loads(line) for line in self.argv_log.read_text().splitlines() if "--max-cost-usd" in line]
        self.assertEqual(load[-1][load[-1].index("--max-cost-usd") + 1], "0", "the load check spends nothing")

    def test_record_and_gate_handle_a_non_ascii_case_name(self) -> None:
        self.make_build(["gold-é", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        self.assertEqual(self.split().plugin_eval, ["gold-é", "trigger-1"])
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)

    def test_record_replaces_a_flow_list_and_keeps_other_keys(self) -> None:
        self.make_build(["gold-01"])
        path = self.ws / "work" / "evaluation" / "data-split.yaml"
        path.write_text('plugin_eval: ["gold-01", "ghost"]  # typed\nplugin_eval_available: 1\ntest: []\n')
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        self.assertEqual((self.split().plugin_eval, self.split().plugin_eval_available), (["gold-01"], 0))
        self.assertIn("\ntest: []\n", path.read_text())

    def test_record_names_each_case_that_fails_to_load(self) -> None:
        self.make_build(["gold-01", "gold-02", "trigger-1"])
        for case in ("gold-01", "trigger-1"):
            (self.ws / "work" / "evaluation" / "plugin" / "evals" / case / "INVALID").write_text("")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3)
        self.assertIn("2 case file(s) fail to load", out.stderr)
        self.assertIn(str(Path("trigger-1", "case.yaml")), out.stderr)

    def test_record_refuses_a_case_named_unlike_its_directory(self) -> None:
        self.make_build(["gold-01"])
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-01" / "case.yaml").write_text("name: g1\n")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3)
        self.assertIn("gold-01 (name: g1); rename one", out.stderr)

    def test_record_refuses_nested_cases_but_not_case_fixtures(self) -> None:
        self.make_build(["gold-01"])
        evals = self.ws / "work" / "evaluation" / "plugin" / "evals"
        (evals / "gold-01" / "fixtures").mkdir()
        (evals / "gold-01" / "fixtures" / "prompt.md").write_text("fixture text")
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0, "a fixture is not a case")
        (evals / "group" / "inner").mkdir(parents=True)
        (evals / "group" / "inner" / "prompt.md").write_text("x")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3)
        self.assertIn("nested eval cases ['group/inner']", out.stderr)

    def test_manual_record_gates_the_test_split_from_ten_gold_standards(self) -> None:
        self.make_build(["gold-01"], invoked=False)
        path = self.ws / "work" / "evaluation" / "data-split.yaml"
        gold = [f"input/g{i:02d}.md" for i in range(10)]
        path.write_text(f"training: [{', '.join(gold[:7])}]\nvalidation: [{', '.join(gold[7:9])}]\ntest: [{gold[9]}]\n")
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        self.assertEqual(self.split().plugin_eval, ["input/g09.md"])
        path.write_text(f"training: [{', '.join(gold)}]\ntest: []\n")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3)
        self.assertIn("10 gold standards and no test split", out.stderr)

    def test_manual_record_gates_every_gold_standard_below_ten(self) -> None:
        self.make_build(["gold-01"], invoked=False)
        path = self.ws / "work" / "evaluation" / "data-split.yaml"
        path.write_text("training: [input/a.md, input/b.md]\nvalidation: [input/c.md]\ntest: [input/d.md]\n")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3, "4 gold standards have no test split (phase-4.md 4.2)")
        self.assertIn("4 gold standards and a test split of 1", out.stderr)
        path.write_text("all_cases: [input/a.md, input/b.md]\ntraining: [input/b.md, input/c.md]\ntest: []\n")
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        self.assertEqual(self.split().plugin_eval, ["input/a.md", "input/b.md", "input/c.md"],
                         "3-9 band: every gold standard, the held-out one included")

    def manual_files(self, *names: str) -> None:
        self.make_build([f"input/{n}" for n in ("input-01.md", "input-02.md")], invoked=False)
        (self.ws / "input").mkdir()
        for name in names:
            (self.ws / "input" / name).write_text(name)

    def test_manual_record_reports_a_gold_standard_left_out_of_the_split(self) -> None:
        self.manual_files("input-01.md", "input-02.md", "input-03.md", "output-01.md", "notes.md")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3, out.stdout)
        self.assertIn("leaves out gold standards found on disk: input/input-03.md;", out.stderr)

    def test_manual_record_reports_a_manifest_gold_standard_left_out(self) -> None:
        self.manual_files("input-01.md", "input-02.md", "edge-case.md")
        (self.ws / "work" / "manifest.yaml").write_text(
            "pairs:\n  - input: input/input-01.md\n  - {input: input/edge-case.md, reference: input/x.md}\n")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3, out.stdout)
        self.assertIn("input/edge-case.md", out.stderr)

    def test_manual_record_reports_manifest_paths_with_spaces_and_quotes(self) -> None:
        """Round 6 adversary: a manifest path with a space, differently named from the listed ones."""
        self.manual_files("input-01.md", "input-02.md", "request one.md", "request two.md", "it's.md", "say \"hi\".md",
                          "flow one.md")
        (self.ws / "work" / "manifest.yaml").write_text(
            "pairs:\n"
            '  - input: "input/request one.md"  # quoted\n'
            "    reference: output-01.md\n"
            "  - input: input/request two.md\n"
            "  - input: 'it''s.md'\n"
            '  - input: "input/say \\"hi\\".md"\n'
            "  - {reference: x.md, input: flow one.md, tags: [test]}\n")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3, out.stdout)
        self.assertIn("leaves out gold standards found on disk: input/flow one.md, input/it's.md, input/request one.md, "
                      'input/request two.md, input/say "hi".md;', out.stderr)

    def test_manifest_inputs_parses_quoted_plain_and_flow_scalars(self) -> None:
        text = ('pairs:\n  - input: "a b.md"  # c\n  - input: c d.md\n  - {input: e f.md, reference: g}\n'
                "  - {reference: g, input: 'h, i.md'}\n  - reference: no-input.md\n  - raw_input: not-me.md\n"
                "  # input: commented.md\n")
        self.assertEqual(gate_common.manifest_inputs(text), ["a b.md", "c d.md", "e f.md", "h, i.md"])

    def test_manual_record_accepts_a_complete_split(self) -> None:
        self.manual_files("input-01.md", "input-02.md", "output-01.md", "output-02.md")
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_manual_record_needs_gold_standards(self) -> None:
        self.make_build([], invoked=False)
        out = self.run_script("gate.py", "record")
        self.assertEqual(out.returncode, 3)
        self.assertIn("lists no gold standards", out.stderr)

    def test_hand_edit_after_the_gate_is_rejected(self) -> None:
        self.make_build(["gold-01", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        path = self.ws / "work" / "evaluation" / "data-split.yaml"
        path.write_text(path.read_text().replace('  - "trigger-1"\n', ""))
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "stale_cases")
        self.assertIn("re-run 5.0", verdict["reason"])

    def test_missing_cases_point_at_record(self) -> None:
        self.make_build(["gold-01", "gold-02"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        record = self.evaluation("gate.json")
        artifact = self.ws / "work" / "evaluation" / "plugin-eval.json"
        data = json.loads(artifact.read_text())
        data["cases"] = data["cases"][:1]
        artifact.write_text(json.dumps(data))
        record["artifact_sha256"] = gate_common.file_sha256(artifact)
        (self.ws / "work" / "evaluation" / "gate.json").write_text(json.dumps(record))
        reason = json.loads(self.run_script("check_gate.py", "--json").stdout)["reason"]
        self.assertIn("missing ['gold-02']", reason)
        self.assertIn("`gate.py record` names it", reason)


class GateLifecycleTest(TempWorkspace):
    CASES = ["gold-01"]

    def detach(self, ws: Path | None = None) -> dict:
        out = self.run_script("gate.py", "--detach", "--model", "m", ws=ws, FAKE_MODE="hang")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stderr, r"gate\.py --workspace \S+ --wait")
        self.assertTrue(_until(lambda: ((ws or self.ws) / "work" / "pids" / "gate-plugin-eval.json").is_file()))
        return self.record("gate", ws)

    def stop(self, ws: Path | None = None) -> None:
        self.assertEqual(self.run_script("gate.py", "--stop", ws=ws).returncode, 0)

    def test_second_start_is_refused_and_stop_ends_the_eval_too(self) -> None:
        self.make_build(self.CASES)
        gate = self.detach()
        try:
            out = self.run_script("gate.py", "--model", "m", FAKE_MODE="hang")
            self.assertEqual(out.returncode, 3)
            self.assertIn("already running", out.stderr)
            self.assertRegex(out.stderr, r"gate\.py --workspace \S+ --wait")
            self.assertIn("--restart", out.stderr)
            self.assertEqual(self.record("gate")["pid"], gate["pid"], "the running gate's record is untouched")
            poll = self.run_script("run_owned.py", "wait", "gate", "--timeout", "0.2")
            self.assertEqual(poll.returncode, 124)
        finally:
            self.stop()
        evals = self.record("gate-plugin-eval")
        self.assertFalse(_group_alive(gate["pgid"]))
        self.assertFalse(_group_alive(evals["pgid"]), "stopping the gate stops the eval run it started")
        self.assertIsNone(self.evaluation("gate.json")["finished"])
        self.assertEqual(self.run_script("check_gate.py", "--json").returncode, 1)

    def test_restart_stops_only_the_recorded_group(self) -> None:
        other = self.root / "other-build"
        other.mkdir()
        self.make_build(self.CASES)
        self.make_build(self.CASES, ws=other)
        old = self.detach()
        old_evals = self.record("gate-plugin-eval")
        neighbour = self.detach(ws=other)
        sibling = subprocess.Popen(old_evals["cmd"], cwd=old_evals["cwd"], env=_env(self.bin, FAKE_MODE="hang"),
                                   **DETACHED)
        try:
            out = self.run_script("gate.py", "--restart", "--model", "m", FAKE_MODE="hang")
            self.assertEqual(out.returncode, 0, out.stderr)
            new = self.record("gate")
            self.assertNotEqual(new["pgid"], old["pgid"])
            self.assertFalse(_group_alive(old["pgid"]), "the recorded gate group is gone")
            self.assertFalse(_group_alive(old_evals["pgid"]), "and so is the eval run it started")
            self.assertTrue(_group_alive(new["pgid"]))
            self.assertIsNone(sibling.poll(), "a process with the eval's exact command line survives")
            self.assertTrue(_group_alive(neighbour["pgid"]), "another build's gate survives")
        finally:
            sibling.kill()
            sibling.wait()
            self.stop()
            self.stop(ws=other)

    def test_run_owned_start_gate_runs_the_gate_in_place(self) -> None:
        self.make_build(self.CASES)
        out = self.run_script("run_owned.py", "start", "gate", "--",
                              *_script("gate.py"), "--workspace", str(self.ws), "--model", "m")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.run_script("run_owned.py", "wait", "gate", "--timeout", "30").returncode, 0)
        self.assertIn("GATE VALID", Path(self.record("gate")["log"]).read_text(encoding="utf-8"))

    def assert_refused_before_detaching(self, *args: str, reason: str, **env: str) -> None:
        out = self.run_script("gate.py", "--detach", "--model", "m", *args, **env)
        self.assertEqual(out.returncode, 3, out.stderr)
        self.assertIn(reason, out.stderr)
        self.assertIn("not started", out.stderr)
        self.assertFalse((self.ws / "work" / "pids" / "gate.json").exists(), "no gate job was started")

    def test_detach_checks_the_suite_first(self) -> None:
        self.make_build(self.CASES)
        (self.ws / "work" / "evaluation" / "plugin" / ".claude-plugin" / "plugin.json").unlink()
        self.assert_refused_before_detaching(reason="needs .claude-plugin/plugin.json")

    def test_detach_checks_for_cases_first(self) -> None:
        self.make_build([])
        (self.ws / "work" / "evaluation" / "plugin" / "evals").mkdir()
        self.assert_refused_before_detaching(reason="no eval cases under")

    def test_detach_checks_that_every_case_loads_first(self) -> None:
        self.make_build(self.CASES)
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-01" / "INVALID").write_text("")
        self.assert_refused_before_detaching(reason="1 case file(s) fail to load")

    def test_detach_checks_each_scaffold_script_first(self) -> None:
        self.make_build(self.CASES)
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-01" / "case.yaml").write_text(
            'name: gold-01\ncontext:\n  scaffold_script: "fixture.sh"  # copies fixtures\n')
        self.assert_refused_before_detaching(reason="gold-01: fixture.sh")

    def test_detach_checks_the_manual_adapter_first(self) -> None:
        self.make_build(self.CASES, invoked=False)
        self.assert_refused_before_detaching("--executor", "single-call", reason="run-case.sh is missing")

    def test_failed_restart_leaves_the_running_gate(self) -> None:
        self.make_build(self.CASES)
        gate = self.detach()
        try:
            (self.ws / "work" / "evaluation" / "plugin" / ".claude-plugin" / "plugin.json").unlink()
            out = self.run_script("gate.py", "--restart", "--model", "m", FAKE_MODE="hang")
            self.assertEqual(out.returncode, 3)
            self.assertIn("the running gate was left as it is", out.stderr)
            self.assertTrue(_group_alive(gate["pgid"]))
            self.assertEqual(self.record("gate")["pid"], gate["pid"])
        finally:
            self.stop()

    def test_wait_blocks_then_prints_one_progress_line_and_exits_124(self) -> None:
        self.make_build(self.CASES)
        self.detach()
        try:
            started = time.monotonic()
            out = self.run_script("gate.py", "--wait", "--wait-timeout", "1")
            self.assertGreaterEqual(time.monotonic() - started, 1)
            self.assertEqual(out.returncode, 124, out.stderr)
            self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)
            self.assertIn("still running after", out.stderr)
            self.assertIn("call --wait again", out.stderr)
            self.assertLess(len(out.stderr), 160, "a short line: every --wait's output stays in the caller's context")
            self.assertEqual(out.stdout, "")
            verbose = self.run_script("gate.py", "--wait", "--wait-timeout", "1", "--verbose")
            self.assertEqual(verbose.returncode, 124, verbose.stderr)
            self.assertIn("gate.log", verbose.stderr, "--verbose names the log")
        finally:
            self.stop()

    def test_wait_returns_the_gate_exit_code_and_its_log(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_adapter()
        out = self.run_script("gate.py", "--detach", "--wait", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("GATE VALID", out.stdout)
        self.assertNotIn("started as owned job", out.stderr, "with --wait the result follows; no start line")
        again = self.run_script("gate.py", "--wait", "--wait-timeout", "1")
        self.assertEqual(again.returncode, 0, "a finished gate returns its exit code at once")
        self.assertTrue(again.stdout.startswith("GATE VALID"), "a passed gate: no routine mode line")
        self.assertNotIn("gate: mode=", again.stdout)
        verbose = self.run_script("gate.py", "--wait", "--wait-timeout", "1", "--verbose")
        self.assertEqual(verbose.returncode, 0)
        self.assertIn("gate: mode=", verbose.stdout, "--verbose keeps the whole log")
        self.assertIn("GATE VALID", verbose.stdout)
        self.write_adapter(SCORING_ADAPTER.replace('echo "EXECUTOR=$GATE_EXECUTOR"', ""))
        out = self.run_script("gate.py", "--detach", "--wait", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("GATE INVALID", out.stdout)

    def test_detach_wait_waits_on_a_running_gate(self) -> None:
        self.make_build(self.CASES)
        gate = self.detach()
        try:
            out = self.run_script("gate.py", "--detach", "--wait", "--model", "m", "--wait-timeout", "1",
                                  FAKE_MODE="hang")
            self.assertEqual(out.returncode, 124, out.stderr)
            self.assertIn("already running; waiting on it", out.stderr)
            self.assertIn("re-issue the same --detach --wait", out.stderr)
            self.assertEqual(self.record("gate")["pid"], gate["pid"], "no second gate was started")
        finally:
            self.stop()

    def test_detach_wait_reports_a_settled_gate_instead_of_re_running_it(self) -> None:
        """Re-issued after the gate finished (the 124 race), it reports that result; a change or --restart re-runs."""
        self.make_build(["input-01"], invoked=False)
        calls = self.root / "calls.log"
        self.write_adapter(SCORING_ADAPTER.replace(
            'echo "EXECUTOR=$GATE_EXECUTOR"', f'echo "EXECUTOR=$GATE_EXECUTOR"; echo run >> {calls}'))

        def gate(*extra: str, model: str = "m") -> tuple[subprocess.CompletedProcess, int]:
            before = len(calls.read_text().splitlines()) if calls.is_file() else 0
            out = self.run_script("gate.py", "--detach", "--wait", "--model", model, "--executor", "single-call",
                                  "--fresh-baseline", *extra)
            return out, len(calls.read_text().splitlines()) - before

        out, runs = gate()
        self.assertEqual((out.returncode, runs), (0, 6), out.stdout + out.stderr)
        out, runs = gate()
        self.assertEqual((out.returncode, runs), (0, 0), out.stdout + out.stderr)
        self.assertIn("its result follows (gate.py --restart re-runs it)", out.stderr)
        self.assertIn("GATE VALID", out.stdout)
        (self.ws / "work" / "pids" / "gate.json").unlink()
        out, runs = gate()
        self.assertEqual((out.returncode, runs), (0, 0), "without the job record, the validator's line")
        self.assertTrue(out.stdout.startswith("GATE VALID"), out.stdout)
        self.assertEqual(gate(model="m2")[1], 6, "other options run a new gate")
        self.assertEqual(gate(model="m2")[1], 0)
        for extra in (["--jobs", "2"], ["--case-timeout", "600"], ["--allow-tools", "Bash", "Write", "Read"],
                      ["--backend", "manual"]):
            with self.subTest(extra=extra):
                self.assertEqual(gate(*extra, model="m2")[1], 6, f"{extra} runs a new gate")
                self.assertEqual(gate(*extra, model="m2")[1], 0, f"{extra} again: settled")
        record = json.loads((self.ws / "work" / "evaluation" / "gate.json").read_text())
        (self.ws / "work" / "evaluation" / "gate.json").write_text(
            json.dumps({k: v for k, v in record.items() if k != "options_sha256"}))
        self.assertEqual(gate("--backend", "manual", model="m2")[1], 6, "a record without the options hash re-runs")
        self.assertEqual(gate("--restart")[1], 6, "--restart always runs it again")
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndisable-model-invocation: true\n"
                                                              "description: Edited.\n---\n")
        self.assertEqual(gate(model="m2")[1], 6, "a changed skill runs a new gate")

    def test_wait_without_a_gate_or_with_bad_options(self) -> None:
        out = self.run_script("gate.py", "--wait")
        self.assertEqual(out.returncode, 3)
        self.assertIn("no `gate` job is recorded", out.stderr)
        for args in (["--wait", "--stop"], ["--wait", "--wait-timeout", "0"], ["record", "--wait"]):
            self.assertEqual(self.run_script("gate.py", *args).returncode, 2, args)

    def test_stop_without_a_gate_is_a_no_op(self) -> None:
        out = self.run_script("gate.py", "--stop")
        self.assertEqual(out.returncode, 0)
        self.assertIn("no running", out.stderr)


class GateManualTest(TempWorkspace):
    def test_user_invoked_skill_runs_the_manual_protocol(self) -> None:
        self.make_build(["input-01", "flaky"], invoked=False)
        self.write_adapter()
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call", "--in-sample")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.evaluation("uplift.json")
        self.assertFalse(data["partial"])
        self.assertEqual([c["with"] for c in data["cases"]], [[0.9] * 3, [0.9] * 3], "the flaky run was retried")
        self.assertEqual(data["cases"][0]["delta"], 0.5)
        self.assertEqual((data["summary"]["in_sample"], data["summary"]["mean_delta"]), (True, 0.5))
        self.assertEqual(self.evaluation("gate.json")["mode"], "manual")
        self.assertFalse(self.argv_log.exists(), "no claude call at all, plugin eval included")
        self.assertIn("gold=2 mean gold delta=+0.500", out.stdout)

    def test_gate_runs_end_to_end_with_no_claude_on_path(self) -> None:
        self.make_build(["input-01", "input-02"], invoked=True, manual=True)
        self.write_adapter()
        self.assertTrue(any(n.startswith("claude") for n in os.listdir(self.bin)),
                        "the fake exists, and no_claude must keep it off PATH")
        record = self.run_script("gate.py", "record", no_claude=True)
        self.assertEqual(record.returncode, 0, record.stderr)
        self.assertIn("mode manual (plugin eval probe exit 127)", record.stdout)
        out = self.run_script("gate.py", "--model", "m", "--executor", "agentic", no_claude=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.evaluation("gate.json")["plugin_eval_available"], 127)
        self.assertFalse(self.argv_log.exists(), "nothing reached a claude binary")
        self.assertEqual(self.run_script("check_gate.py", no_claude=True).returncode, 0)

    def test_user_invoked_skill_never_calls_claude(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_adapter()
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertFalse(self.argv_log.exists(), "the probe is skipped for a user-invoked skill")
        split = gate_common.read_data_split(self.ws / "work" / "evaluation" / "data-split.yaml")
        self.assertEqual(split.plugin_eval_available, "not-probed")

    def test_unavailable_plugin_eval_routes_to_manual(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        self.write_adapter()
        out = self.run_script("gate.py", "--model", "m", "--executor", "agentic", FAKE_HELP_RC="1")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.evaluation("gate.json")["plugin_eval_available"], 1)

    def test_another_host_runs_the_manual_protocol_even_with_claude_installed(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        self.write_adapter()
        record = self.run_script("gate.py", "record", CLAUDECODE="")
        self.assertEqual(record.returncode, 0, record.stderr)
        self.assertIn("mode manual (plugin eval probe exit not-probed)", record.stdout)
        out = self.run_script("gate.py", "--model", "gpt-5.6", "--executor", "agentic", CLAUDECODE="")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual((self.evaluation("gate.json")["mode"], self.evaluation("gate.json")["backend"]),
                         ("manual", "manual"))
        self.assertFalse(self.argv_log.exists(), "the other host's model id never reached claude")

    def test_backend_manual_inside_claude_code_is_refused_without_force_manual(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        self.write_adapter()
        for args in (["record", "--backend", "manual"],
                     ["--detach", "--backend", "manual", "--model", "m", "--executor", "agentic"],
                     ["--backend", "manual", "--model", "m", "--executor", "agentic"]):
            out = self.run_script("gate.py", *args)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("--force-manual", out.stdout + out.stderr)
            if args[0] == "--detach":
                self.assertFalse((self.ws / "work" / "pids" / "gate.json").exists(), "no gate job was started")
        self.assertFalse((self.ws / "work" / "evaluation" / "gate.json").exists(), "no gate record was written")
        self.assertFalse((self.ws / "work" / "evaluation" / "uplift.json").exists())
        attempts = gate_common.read_attempts(self.ws)
        self.assertEqual([a["step"] for a in attempts], ["record", "gate", "gate"])
        self.assertTrue(all("--backend manual inside Claude Code" in a["error"] for a in attempts))

    def test_backend_manual_still_runs_where_plugin_eval_cannot_or_the_skill_is_user_invoked(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        self.write_adapter()
        out = self.run_script("gate.py", "--backend", "manual", "--model", "m", "--executor", "agentic",
                              FAKE_HELP_RC="1")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.make_build(["input-01"], invoked=False, ws=self.root / "user-invoked")
        self.write_adapter_to(self.root / "user-invoked")
        out = self.run_script("gate.py", "--backend", "manual", "--model", "m", "--executor", "single-call",
                              ws=self.root / "user-invoked")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_force_manual_runs_unprobed_and_logs_the_reason(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        self.write_adapter()
        reason = "cursor-agent:  the benchmark launched it\nfrom a Claude Code shell"
        out = self.run_script("gate.py", "--backend", "manual", "--force-manual", reason, "--model", "gpt-5.6",
                              "--executor", "agentic")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        logged = "cursor-agent: the benchmark launched it from a Claude Code shell"
        record = self.evaluation("gate.json")
        self.assertEqual((record["mode"], record["force_manual"]), ("manual", logged))
        split = (self.ws / "work" / "evaluation" / "data-split.yaml").read_text(encoding="utf-8")
        self.assertIn(logged, split)
        self.assertEqual(gate_common.read_data_split(self.ws / "work" / "evaluation" / "data-split.yaml")
                         .plugin_eval_available, "not-probed")
        self.assertFalse(self.argv_log.exists(), "a forced manual gate never calls claude")
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)

    def test_force_manual_needs_backend_manual_and_a_host_and_reason(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        for args in (["record", "--force-manual", "cursor: why"],
                     ["record", "--backend", "plugin", "--force-manual", "cursor: why"],
                     ["record", "--backend", "manual", "--force-manual", "no colon"],
                     ["record", "--backend", "manual", "--force-manual", "cursor:  "]):
            out = self.run_script("gate.py", *args)
            self.assertEqual(out.returncode, 2, args)
            self.assertIn("--force-manual goes with --backend manual", out.stderr)
        for host in ("Claude Code", "claude-code", "CLAUDE_CODE"):
            out = self.run_script("gate.py", "record", "--backend", "manual", "--force-manual", f"{host}: faster")
            self.assertEqual(out.returncode, 2, host)
            self.assertIn("never Claude Code", out.stderr)

    def test_backend_plugin_that_cannot_run_is_a_precondition_error(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        out = self.run_script("gate.py", "record", "--backend", "plugin", CLAUDECODE="", no_claude=True)
        self.assertEqual(out.returncode, 3)
        self.assertIn("--backend plugin needs `claude plugin eval`", out.stderr)
        self.make_build([], invoked=False, ws=self.root / "user-invoked")
        out = self.run_script("gate.py", "record", "--backend", "plugin", ws=self.root / "user-invoked")
        self.assertEqual(out.returncode, 3)
        self.assertIn("a user-invoked skill never fires", out.stderr)

    def test_run_timeout_leaves_a_partial_artifact(self) -> None:
        self.make_build(["input-01", "slow"], invoked=False)
        self.write_adapter()
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call", "--runs", "1",
                              "--case-timeout", "0.5")
        self.assertEqual(out.returncode, 1)
        data = self.evaluation("uplift.json")
        self.assertTrue(data["partial"])
        self.assertTrue(all("timed out" in e["error"] for e in data["errors"]))
        self.assertIn("uplift.json is partial", self.run_script("check_gate.py").stdout)

    def test_stop_ends_running_manual_cases(self) -> None:
        self.make_build(["slow"], invoked=False)
        self.write_adapter()
        out = self.run_script("gate.py", "--detach", "--model", "m", "--executor", "single-call", "--runs", "1")
        self.assertEqual(out.returncode, 0, out.stderr)
        pids = self.ws / "work" / "pids"
        self.assertTrue(_until(lambda: len(list(pids.glob("gate-c00-*.json"))) == 2))
        runs = [json.loads(p.read_text()) for p in pids.glob("gate-c00-*.json")]
        self.assertEqual(self.run_script("gate.py", "--stop").returncode, 0)
        self.assertTrue(all(not _group_alive(r["pgid"]) for r in runs), "every running case was stopped")

    def manual_gate(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_adapter()
        (self.ws / "input").mkdir()
        (self.ws / "input" / "po-01.json").write_text('{"approval": "director"}')
        (self.ws / "work" / "evaluation" / "score.py").write_text("print('SCORE=1')\n")
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        record = self.evaluation("gate.json")
        self.assertEqual(sorted(record["inputs_sha256"]), [
            "input/po-01.json", "work/evaluation/plugin/.claude-plugin/plugin.json",
            "work/evaluation/plugin/evals/input-01/prompt.md", "work/evaluation/score.py"])
        self.assertEqual(record["artifact_sha256"],
                         gate_common.file_sha256(self.ws / "work" / "evaluation" / "uplift.json"))

    def assert_stale(self, changed: str) -> None:
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "stale_inputs")
        self.assertTrue(verdict["reason"].startswith("gate inputs changed since the gate; re-run 5.0"))
        self.assertIn(changed, verdict["reason"])

    def test_what_the_gate_writes_is_not_an_input(self) -> None:
        self.manual_gate()
        evaluation = self.ws / "work" / "evaluation"
        for generated in (".cases.tmp", "uplift.prev.json", "plugin/skills/demo/SKILL.md",
                          "plugin/evals/results/run-1/report.html", "baseline-cache/scratch.json"):
            (evaluation / generated).parent.mkdir(parents=True, exist_ok=True)
            (evaluation / generated).write_text("x")
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)

    def test_golden_out_file_after_the_gate_is_stale(self) -> None:
        self.manual_gate()
        (self.ws / "work" / "evaluation" / "expected.out").write_text("golden")
        self.assert_stale("work/evaluation/expected.out")

    def test_plugin_manifest_or_shared_mock_edit_after_the_gate_is_stale(self) -> None:
        self.manual_gate()
        (self.ws / "work" / "evaluation" / "plugin" / ".claude-plugin" / "plugin.json").write_text("{}")
        self.assert_stale("work/evaluation/plugin/.claude-plugin/plugin.json")
        (self.ws / "work" / "evaluation" / "plugin" / "mocks").mkdir()
        (self.ws / "work" / "evaluation" / "plugin" / "mocks" / "api.json").write_text("{}")
        self.assert_stale("work/evaluation/plugin/mocks/api.json")

    def test_adapter_edit_after_the_gate_is_stale(self) -> None:
        self.manual_gate()
        self.write_adapter(SCORING_ADAPTER.replace("SCORE=0.4", "SCORE=0.0"))
        self.assert_stale("work/evaluation/run-case.sh")

    def test_scorer_edit_after_the_gate_is_stale(self) -> None:
        self.manual_gate()
        (self.ws / "work" / "evaluation" / "score.py").write_text("print('SCORE=0')\n")
        self.assert_stale("work/evaluation/score.py")

    def test_fixture_edit_or_addition_after_the_gate_is_stale(self) -> None:
        self.manual_gate()
        (self.ws / "input" / "po-02.json").write_text("{}")
        self.assert_stale("input/po-02.json")

    def test_artifact_edit_after_the_gate_is_stale(self) -> None:
        self.manual_gate()
        path = self.ws / "work" / "evaluation" / "uplift.json"
        path.write_text(path.read_text().replace("0.4", "0.1"))
        self.assert_stale("uplift.json is not the artifact the gate wrote")

    def test_an_input_changed_while_the_gate_runs_leaves_no_finished_run(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_adapter(SCORING_ADAPTER.replace(
            'echo "EXECUTOR=$GATE_EXECUTOR"', 'echo "EXECUTOR=$GATE_EXECUTOR"; echo x >> work/evaluation/fixture.txt'))
        (self.ws / "work" / "evaluation" / "fixture.txt").write_text("start\n")
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("gate inputs changed while the gate ran (inputs_sha256)", out.stdout + out.stderr)
        record = self.evaluation("gate.json")
        self.assertIsNone(record["finished"])
        self.assertEqual(record["changed_during_run"], ["inputs_sha256"])
        self.assertFalse((self.ws / "work" / "evaluation" / "baseline-cache").exists(),
                         "a without arm run on changing inputs is never cached")
        self.assertEqual(json.loads(self.run_script("check_gate.py", "--json").stdout)["code"], "gate_unfinished")

    def test_a_run_without_the_executor_receipt_fails(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_adapter(SCORING_ADAPTER.replace('echo "EXECUTOR=$GATE_EXECUTOR"', 'echo "EXECUTOR=single-call"'))
        out = self.run_script("gate.py", "--model", "m", "--executor", "agentic", "--runs", "1")
        self.assertEqual(out.returncode, 1)
        data = self.evaluation("uplift.json")
        self.assertTrue(data["partial"])
        self.assertIn("no EXECUTOR=agentic line", data["errors"][0]["error"])
        self.assertIn("got single-call", data["errors"][0]["error"])
        self.write_adapter(SCORING_ADAPTER.replace('echo "EXECUTOR=$GATE_EXECUTOR"', ""))
        self.run_script("gate.py", "--model", "m", "--executor", "agentic", "--runs", "1")
        self.assertIn("got none", self.evaluation("uplift.json")["errors"][0]["error"])

    def test_a_split_change_after_the_gate_is_stale_but_a_comment_is_not(self) -> None:
        self.make_build(["input-01", "input-02"], invoked=False)
        self.write_adapter()
        self.assertEqual(self.run_script("gate.py", "--model", "m", "--executor", "single-call").returncode, 0)
        split = self.ws / "work" / "evaluation" / "data-split.yaml"
        split.write_text("# reviewed\n" + split.read_text())
        self.assertEqual(self.run_script("check_gate.py").returncode, 0, "comments and layout are not the splits")
        split.write_text(split.read_text().replace("training: []", 'training: ["input-02"]'))
        self.assert_stale("work/evaluation/data-split.yaml (its splits)")

    def baseline_build(self) -> Path:
        """A user-invoked build whose adapter logs each run's `<case> <arm>` to work/pids/calls.log."""
        self.make_build(["input-01", "input-02"], invoked=False)
        self.write_adapter(SCORING_ADAPTER.replace(
            'echo "EXECUTOR=$GATE_EXECUTOR"', 'echo "EXECUTOR=$GATE_EXECUTOR"; echo "$case $arm" >> work/pids/calls.log'))
        (self.ws / "work" / "evaluation" / "score.py").write_text("print('SCORE=1')\n")
        return self.ws / "work" / "pids" / "calls.log"

    def gate_runs(self, calls: Path, *args: str) -> tuple[subprocess.CompletedProcess, int, int]:
        """One manual gate; (its output, with-arm runs, without-arm runs) it spawned."""
        def counts() -> tuple[int, int]:
            lines = calls.read_text().splitlines() if calls.is_file() else []
            return sum(line.endswith(" with") for line in lines), sum(line.endswith(" without") for line in lines)
        with_before, without_before = counts()
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call", *args)
        with_after, without_after = counts()
        return out, with_after - with_before, without_after - without_before

    def test_run_case_runs_as_an_executor(self) -> None:
        """CSA_EXECUTOR=1 reaches run-case.sh, so a guarded executor it starts keeps the finish guard inert."""
        self.make_build(["input-01"], invoked=False)
        self.write_adapter(SCORING_ADAPTER.replace(
            'echo "EXECUTOR=$GATE_EXECUTOR"', 'echo "EXECUTOR=$GATE_EXECUTOR"; echo "${CSA_EXECUTOR:-unset}" >> work/pids/marker'))
        (self.ws / "work" / "evaluation" / "score.py").write_text("print('SCORE=1')\n")
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(set((self.ws / "work" / "pids" / "marker").read_text().split()), {"1"})

    def test_a_re_gate_reuses_the_cached_without_arm(self) -> None:
        calls = self.baseline_build()
        out, with_runs, without_runs = self.gate_runs(calls)
        self.assertEqual((out.returncode, with_runs, without_runs), (0, 6, 6), out.stdout + out.stderr)
        cache = self.ws / "work" / "evaluation" / "baseline-cache"
        self.assertEqual(len(list(cache.glob("*.json"))), 2)
        self.assertNotIn("without_cache", json.dumps(self.evaluation("uplift.json")))
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndisable-model-invocation: true\n"
                                                              "description: Demo.\n---\n# Demo, revised\n")
        out, with_runs, without_runs = self.gate_runs(calls)
        self.assertEqual((out.returncode, with_runs, without_runs), (0, 6, 0), out.stdout + out.stderr)
        self.assertIn("reusing the cached without-skill arm for 2 of 2 cases", out.stdout)
        data = self.evaluation("uplift.json")
        self.assertEqual([c["without"] for c in data["cases"]], [[0.4] * 3] * 2)
        for case in data["cases"]:
            path = cache / f"{case['without_cache']['key']}.json"
            self.assertEqual(case["without_cache"]["sha256"], gate_common.file_sha256(path))
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)
        out, with_runs, without_runs = self.gate_runs(calls, "--fresh-baseline")
        self.assertEqual((out.returncode, with_runs, without_runs), (0, 6, 6), out.stdout + out.stderr)
        self.assertNotIn("without_cache", json.dumps(self.evaluation("uplift.json")))
        self.assertTrue(self.evaluation("gate.json")["fresh_baseline"])

    def test_a_grader_change_misses_the_cache(self) -> None:
        calls = self.baseline_build()
        self.assertEqual(self.gate_runs(calls)[0].returncode, 0)
        (self.ws / "work" / "evaluation" / "score.py").write_text("print('SCORE=0.5')\n")
        out, _, without_runs = self.gate_runs(calls)
        self.assertEqual((out.returncode, without_runs), (0, 6), out.stdout + out.stderr)
        self.assertNotIn("reusing", out.stdout)
        self.write_adapter(SCORING_ADAPTER.replace('echo "EXECUTOR', 'echo "$case $arm" >> work/pids/calls.log; echo "EXECUTOR'))
        out, _, without_runs = self.gate_runs(calls)
        self.assertEqual((out.returncode, without_runs), (0, 6), "a run-case.sh edit misses it too")
        out = self.run_script("gate.py", "--model", "other", "--executor", "single-call")
        self.assertNotIn("reusing", out.stdout, "so does another model")

    def test_a_stale_or_missing_cache_entry_fails_the_gate_that_reused_it(self) -> None:
        calls = self.baseline_build()
        self.assertEqual(self.gate_runs(calls)[0].returncode, 0)
        self.assertEqual(self.gate_runs(calls)[2], 0)
        entry = self.ws / "work" / "evaluation" / "baseline-cache" / \
            f"{self.evaluation('uplift.json')['cases'][0]['without_cache']['key']}.json"
        original = entry.read_bytes()
        entry.write_bytes(original.replace(b"0.4", b"0.1"))
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "stale_baseline")
        self.assertIn("not the entry the gate reused", verdict["reason"])
        entry.unlink()
        self.assertEqual(json.loads(self.run_script("check_gate.py", "--json").stdout)["code"], "stale_baseline")
        entry.write_bytes(original)
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)
        case = dict(self.evaluation("uplift.json")["cases"][0])
        with self.assertRaises(gate.check_gate.Invalid) as raised:
            gate.check_gate._reused_baseline(self.ws, case["case"], case, {"model": "other", "executor": "single-call"})
        self.assertIn("key no longer matches", raised.exception.reason)
        case["without_cache"] = {"key": "../../escape", "sha256": "x"}
        with self.assertRaises(gate.check_gate.Invalid):
            gate.check_gate._reused_baseline(self.ws, case["case"], case, {"model": "m", "executor": "single-call"})

    def test_a_malformed_cache_entry_is_re_run_not_reused(self) -> None:
        calls = self.baseline_build()
        self.assertEqual(self.gate_runs(calls)[0].returncode, 0)
        for entry in (self.ws / "work" / "evaluation" / "baseline-cache").glob("*.json"):
            entry.write_text(json.dumps({**json.loads(entry.read_text()), "runs": [0.4, 0.4]}))
        out, _, without_runs = self.gate_runs(calls)
        self.assertEqual((out.returncode, without_runs), (0, 6), out.stdout + out.stderr)

    def test_missing_adapter_is_a_precondition_error(self) -> None:
        self.make_build(["input-01"], invoked=False)
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 3)
        self.assertIn("run-case.sh is missing", out.stdout)


class GateJobTest(TempWorkspace):
    def test_inside_gate_job_needs_the_records_token(self) -> None:
        pids = self.ws / "work" / "pids"
        pids.mkdir(parents=True)
        (pids / "gate.json").write_text(json.dumps({
            "name": "gate", "pid": 1, "pgid": 1, "lstart": None, "cmd": [], "cwd": "", "log": "", "rc_file": "",
            "started": "", "token": "abc"}))
        with mock.patch.dict(os.environ, {"RUN_OWNED_JOB": "gate", "RUN_OWNED_TOKEN": "abc"}):
            self.assertTrue(gate._inside_gate_job(self.ws))
        with mock.patch.dict(os.environ, {"RUN_OWNED_JOB": "gate", "RUN_OWNED_TOKEN": "inherited"}):
            self.assertFalse(gate._inside_gate_job(self.ws))


if __name__ == "__main__":
    unittest.main()
