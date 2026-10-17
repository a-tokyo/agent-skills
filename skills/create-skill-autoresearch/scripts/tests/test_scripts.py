"""Offline tests for run_owned.py, gate.py and check_gate.py.

A fake `claude` on PATH stands in for Claude Code, fake run-case.sh adapters stand in for the
build's executor, and every workspace is a temp dir. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/scripts/tests -v
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import gate_common  # noqa: E402

FAKE_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Fake `claude plugin eval`; behaviour from FAKE_MODE: complete | partial | hang | fewruns | silent."""
    import json, os, sys, time
    from pathlib import Path
    args = sys.argv[1:]
    log = os.environ.get("FAKE_ARGV_LOG")
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps(args) + "\\n")
    if args[:3] == ["plugin", "eval", "--help"]:
        sys.exit(int(os.environ.get("FAKE_HELP_RC", "0")))
    mode = os.environ.get("FAKE_MODE", "complete")
    if mode == "hang":
        time.sleep(300)
    if mode == "silent":
        sys.exit(1)
    runs = int(args[args.index("--runs") + 1])
    if mode == "fewruns":
        runs -= 1
    out = args[args.index("--json") + 1]
    cases = sorted(p.name for p in Path("evals").iterdir() if (p / "prompt.md").is_file())
    run = {"score": 1, "passed": True, "error": None, "graders": []}
    bare = {"score": 0.5, "passed": False, "error": None, "graders": []}
    data = {
        "schemaVersion": 1, "partial": mode == "partial",
        "partialReason": "interrupted" if mode == "partial" else None,
        "skillCopy": sorted(p.name for p in Path("skills").rglob("*") if p.is_file()),
        "cases": [{"name": c, "arms": {"with": [run] * runs, "without": [bare] * runs},
                   "aggregates": {"delta": 0.5}} for c in cases],
        "aggregates": {"meanDelta": 0.5},
    }
    Path(out).write_text(json.dumps(data))
''')

SCORING_ADAPTER = textwrap.dedent('''\
    #!/bin/sh
    # with -> 0.9, without -> 0.4; case "flaky" fails its first attempt of run 2; "slow" hangs
    case="$1"; arm="$2"
    marker="$(dirname "$0")/.flaky-$arm"
    if [ "$case" = "flaky" ] && [ "$GATE_RUN" = "2" ] && [ ! -f "$marker" ]; then
      : > "$marker"; echo "transient failure" >&2; exit 1
    fi
    if [ "$case" = "slow" ]; then sleep 300; fi
    echo "running $case $arm on $GATE_MODEL"
    if [ "$arm" = "with" ]; then echo "SCORE=0.9"; else echo "SCORE=0.4"; fi
''')


def _env(bin_dir: Path, **extra: str) -> dict[str, str]:
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "PYTHONDONTWRITEBYTECODE": "1", **extra}


def _script(name: str) -> list[str]:
    return [sys.executable, "-B", str(SCRIPTS / name)]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


class TempWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        self.argv_log = self.root / "claude-argv.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_build(self, cases: list[str], invoked: bool = True) -> None:
        skill = self.ws / "output" / "demo"
        skill.mkdir(parents=True)
        flag = "" if invoked else "disable-model-invocation: true\n"
        (skill / "SKILL.md").write_text(f"---\nname: demo\n{flag}description: Demo.\n---\n# Demo\n")
        evaluation = self.ws / "work" / "evaluation"
        evaluation.mkdir(parents=True)
        listed = "\n".join(f'  - "{c}"' for c in cases)
        (evaluation / "data-split.yaml").write_text(
            f"training: []\nplugin_eval_available: 0  # probe exit code\nplugin_eval:\n{listed}\n")
        plugin = evaluation / "plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "demo-eval", "version": "0.0.0"}')
        for case in cases:
            (plugin / "evals" / case).mkdir(parents=True)
            (plugin / "evals" / case / "prompt.md").write_text(f"Do {case}.\n")

    def write_adapter(self, body: str = SCORING_ADAPTER) -> None:
        adapter = self.ws / "work" / "evaluation" / "run-case.sh"
        adapter.write_text(body)
        adapter.chmod(0o755)

    def run_script(self, name: str, *args: str, timeout: float = 60, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([*_script(name), "--workspace", str(self.ws), *args], capture_output=True,
                              text=True, timeout=timeout, env=_env(self.bin, FAKE_ARGV_LOG=str(self.argv_log), **env))

    def evaluation(self, name: str) -> dict:
        return json.loads((self.ws / "work" / "evaluation" / name).read_text())


class RunOwnedTest(TempWorkspace):
    def start(self, name: str, *cmd: str) -> dict:
        out = self.run_script("run_owned.py", "start", name, "--", *cmd)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_wait_returns_the_job_exit_code(self) -> None:
        self.start("seven", "sh", "-c", "echo hi; exit 7")
        out = self.run_script("run_owned.py", "wait", "seven", "--timeout", "10")
        self.assertEqual(out.returncode, 7)
        status = json.loads(self.run_script("run_owned.py", "status", "seven").stdout)
        self.assertEqual((status["state"], status["exit_code"]), ("exited", 7))
        self.assertIn("hi", Path(status["log"]).read_text())

    def test_stop_kills_only_its_own_group(self) -> None:
        sibling = subprocess.Popen(["sleep", "300"], start_new_session=True)
        try:
            job = self.start("mine", "sh", "-c", "sleep 300 & sleep 300; wait")
            self.assertTrue(_group_alive(job["pgid"]))
            out = self.run_script("run_owned.py", "stop", "mine", "--grace", "5")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertFalse(_group_alive(job["pgid"]), "every process in the owned group is gone")
            self.assertIsNone(sibling.poll(), "a sibling with the same command line survives")
            status = json.loads(self.run_script("run_owned.py", "status", "mine").stdout)
            self.assertEqual(status["state"], "stopped")
        finally:
            sibling.kill()
            sibling.wait()

    def test_wait_timeout_leaves_the_job_running(self) -> None:
        job = self.start("slow", "sleep", "30")
        out = self.run_script("run_owned.py", "wait", "slow", "--timeout", "0.5")
        self.assertEqual(out.returncode, 124)
        self.assertIn("still running", out.stderr)
        self.assertTrue(_alive(job["pid"]))
        self.assertEqual(self.run_script("run_owned.py", "stop", "slow").returncode, 0)
        self.assertFalse(_group_alive(job["pgid"]))

    def test_term_ignoring_job_is_killed_after_grace(self) -> None:
        job = self.start("stubborn", "sh", "-c", "trap '' TERM; sleep 300")
        time.sleep(0.3)
        out = self.run_script("run_owned.py", "stop", "stubborn", "--grace", "0.5")
        self.assertIn("killed after", out.stdout)
        self.assertFalse(_group_alive(job["pgid"]))

    def test_refuses_names_it_did_not_start(self) -> None:
        out = self.run_script("run_owned.py", "stop", "never-started")
        self.assertEqual(out.returncode, 3)
        self.assertIn("no record", out.stderr)
        self.assertEqual(self.run_script("run_owned.py", "stop", "../escape").returncode, 3)

    def test_refuses_a_reused_pid(self) -> None:
        stranger = subprocess.Popen(["sleep", "300"], start_new_session=True)
        try:
            pids = self.ws / "work" / "pids"
            pids.mkdir(parents=True)
            (pids / "forged.json").write_text(json.dumps({
                "name": "forged", "pid": stranger.pid, "pgid": stranger.pid, "lstart": "Mon Jan  1 00:00:00 2001",
                "cmd": ["sleep", "300"], "cwd": str(self.ws), "log": str(pids / "forged.log"),
                "rc_file": str(pids / "forged.rc"), "started": "2001-01-01T00:00:00+00:00", "stopped": None}))
            out = self.run_script("run_owned.py", "stop", "forged")
            self.assertEqual(out.returncode, 3)
            self.assertIn("another process", out.stderr)
            self.assertIsNone(stranger.poll())
        finally:
            stranger.kill()
            stranger.wait()

    def test_refuses_to_start_a_running_name_twice(self) -> None:
        self.start("once", "sleep", "30")
        out = self.run_script("run_owned.py", "start", "once", "--", "sleep", "30")
        self.assertEqual(out.returncode, 3)
        self.assertIn("already running", out.stderr)
        self.run_script("run_owned.py", "stop", "once")


class DataSplitReaderTest(TempWorkspace):
    def read(self, text: str) -> gate_common.DataSplit:
        path = self.ws / "data-split.yaml"
        path.write_text(text)
        return gate_common.read_data_split(path)

    def test_flow_and_block_lists(self) -> None:
        self.assertEqual(self.read("plugin_eval: [a, 'b', \"c\"]  # note\n").plugin_eval, ["a", "b", "c"])
        split = self.read("plugin_eval_available: 0\nplugin_eval:\n  # comment\n  - gold-01\n  - \"gold-02\"\nnext: 1\n")
        self.assertEqual((split.plugin_eval, split.plugin_eval_available), (["gold-01", "gold-02"], 0))

    def test_rejects_shapes_it_does_not_read(self) -> None:
        self.assertIn("list of case names", self.read("plugin_eval:\n  cases: [a, b]\n").plugin_eval_error)
        self.assertIn("mapping", self.read("plugin_eval: {cases: [a, b]}\n").plugin_eval_error)
        self.assertIn("scalar", self.read("plugin_eval: not_scaffolded\n").plugin_eval_error)
        self.assertIn("twice", self.read("plugin_eval: [a, a]\n").plugin_eval_error)
        self.assertEqual(self.read("plugin_eval_available: manual\n").plugin_eval_available, "manual")


class GatePluginTest(TempWorkspace):
    CASES = ["gold-01", "gold-02", "trigger-1"]

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
        self.assertEqual(self.evaluation("gate.json")["mode"], "plugin")
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)

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
        self.assertIn("timed out", out.stderr)
        record = json.loads((self.ws / "work" / "pids" / "gate-plugin-eval.json").read_text())
        self.assertIsNotNone(record["stopped"])
        self.assertFalse(_group_alive(record["pgid"]))

    def test_stale_artifact_never_passes(self) -> None:
        self.make_build(self.CASES)
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        out = self.run_script("gate.py", "--model", "m", FAKE_MODE="silent")
        self.assertEqual(out.returncode, 1)
        self.assertIn("plugin-eval.json not found", out.stdout)
        self.assertTrue((self.ws / "work" / "evaluation" / "plugin-eval.prev.json").is_file())

    def test_unrecorded_eval_case_is_a_precondition_error(self) -> None:
        self.make_build(self.CASES)
        extra = self.ws / "work" / "evaluation" / "plugin" / "evals" / "extra"
        extra.mkdir()
        (extra / "prompt.md").write_text("x")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3)
        self.assertIn("present but unrecorded ['extra']", out.stderr)


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
        self.assertFalse((self.argv_log.read_text().count("--json")), "plugin eval never ran")

    def test_unavailable_plugin_eval_routes_to_manual(self) -> None:
        self.make_build(["gold-01"], invoked=True)
        self.write_adapter()
        out = self.run_script("gate.py", "--model", "m", "--executor", "agentic", FAKE_HELP_RC="1")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.evaluation("gate.json")["plugin_eval_available"], 1)

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

    def test_missing_adapter_is_a_precondition_error(self) -> None:
        self.make_build(["input-01"], invoked=False)
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 3)
        self.assertIn("run-case.sh is missing", out.stderr)


class CheckGateTest(TempWorkspace):
    def write_uplift(self, cases: list[str], runs: int = 3, where: str = "work/evaluation") -> None:
        path = self.ws / where / "uplift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cases": [{"case": c, "with": [1.0] * runs, "without": [0.0] * runs}
                                              for c in cases]}))

    def test_missing_artifact(self) -> None:
        self.make_build(["input-01"], invoked=False)
        out = self.run_script("check_gate.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("mode manual: work/evaluation/uplift.json not found", out.stdout)

    def test_wrong_path_is_missing(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_uplift(["input-01"], where="benchmarks/demo")
        self.assertIn("uplift.json not found", self.run_script("check_gate.py").stdout)

    def test_derived_mode_without_gate_record(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_uplift(["input-01"])
        out = self.run_script("check_gate.py")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("mode=manual", out.stdout)
        self.assertIn("mean delta=+1.000", out.stdout)

    def test_too_few_runs(self) -> None:
        self.make_build(["input-01"], invoked=False)
        self.write_uplift(["input-01"], runs=2)
        self.assertIn("has 2 runs; 3 required", self.run_script("check_gate.py").stdout)

    def test_case_coverage_must_match_exactly(self) -> None:
        self.make_build(["input-01", "input-02"], invoked=False)
        self.write_uplift(["input-01", "input-03"])
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
        path.write_text(json.dumps({"cases": [{"case": "input-01", "with": [0.5] * 3, "without": [0.5] * 3}]}))
        out = self.run_script("check_gate.py", "--json")
        self.assertEqual(json.loads(out.stdout)["non_positive"], ["input-01"])


if __name__ == "__main__":
    unittest.main()
