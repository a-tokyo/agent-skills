"""Offline tests for run_owned.py, gate.py and check_gate.py.

A fake `claude` on PATH stands in for Claude Code, fake run-case.sh adapters stand in for the
build's executor, and every workspace is a temp dir. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/scripts/tests -v
"""

from __future__ import annotations

import json
import os
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
    """Fake `claude plugin eval`; behaviour from FAKE_MODE: complete | partial | hang | fewruns | silent.
    Cases named trigger-* get a skill-fired grader, near-miss* a not-fired one (max 0)."""
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

    def case(name):
        if name.startswith("trigger"):
            grader = {"name": "skill-fired", "type": "tool_used", "config": {"tool": "Skill"}}
            run = {"score": 1, "error": None, "graders": [{"name": "skill-fired", "passed": True}]}
            return {"name": name, "graders": [grader], "arms": {"with": [run] * runs, "without": [run] * runs},
                    "aggregates": {"delta": 0}}
        if name.startswith("near-miss"):
            grader = {"name": "not-fired", "type": "tool_used", "config": {"tool": "Skill", "min": 0, "max": 0}}
            run = {"score": 1, "error": None, "graders": [{"name": "not-fired", "passed": True}]}
            return {"name": name, "graders": [grader], "arms": {"with": [run] * runs, "without": [run] * runs},
                    "aggregates": {"delta": 0}}
        grader = {"name": "fact", "type": "regex", "config": {}}
        good = {"score": 1, "error": None, "graders": [{"name": "fact", "passed": True}]}
        bare = {"score": 0.5, "error": None, "graders": [{"name": "fact", "passed": False}]}
        return {"name": name, "graders": [grader], "arms": {"with": [good] * runs, "without": [bare] * runs},
                "aggregates": {"delta": 0.5}}

    cases = sorted(p.name for p in Path("evals").iterdir() if (p / "prompt.md").is_file())
    data = {
        "schemaVersion": 1, "partial": mode == "partial",
        "partialReason": "interrupted" if mode == "partial" else None,
        "skillCopy": sorted(p.name for p in Path("skills").rglob("*") if p.is_file()),
        "cases": [case(c) for c in cases],
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
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _until(predicate, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


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

    def make_build(self, cases: list[str], invoked: bool = True, ws: Path | None = None) -> None:
        ws = ws or self.ws
        skill = ws / "output" / "demo"
        skill.mkdir(parents=True)
        flag = "" if invoked else "disable-model-invocation: true\n"
        (skill / "SKILL.md").write_text(f"---\nname: demo\n{flag}description: Demo.\n---\n# Demo\n")
        evaluation = ws / "work" / "evaluation"
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

    def run_script(self, name: str, *args: str, timeout: float = 60, ws: Path | None = None,
                   **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([*_script(name), "--workspace", str(ws or self.ws), *args], capture_output=True,
                              text=True, timeout=timeout, env=_env(self.bin, FAKE_ARGV_LOG=str(self.argv_log), **env))

    def evaluation(self, name: str) -> dict:
        return json.loads((self.ws / "work" / "evaluation" / name).read_text())

    def record(self, name: str, ws: Path | None = None) -> dict:
        return json.loads(((ws or self.ws) / "work" / "pids" / f"{name}.json").read_text())


class RunOwnedTest(TempWorkspace):
    def start(self, name: str, *cmd: str) -> dict:
        out = self.run_script("run_owned.py", "start", name, "--", *cmd)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def forge(self, stranger: subprocess.Popen, lstart: str | None) -> None:
        pids = self.ws / "work" / "pids"
        pids.mkdir(parents=True)
        (pids / "forged.json").write_text(json.dumps({
            "name": "forged", "pid": stranger.pid, "pgid": stranger.pid, "lstart": lstart,
            "cmd": ["sleep", "300"], "cwd": str(self.ws), "log": str(pids / "forged.log"),
            "rc_file": str(pids / "forged.rc"), "started": "2001-01-01T00:00:00+00:00", "stopped": None}))

    def test_wait_returns_the_job_exit_code(self) -> None:
        self.start("seven", "sh", "-c", "echo hi; exit 7")
        out = self.run_script("run_owned.py", "wait", "seven", "--timeout", "10")
        self.assertEqual(out.returncode, 7)
        status = json.loads(self.run_script("run_owned.py", "status", "seven").stdout)
        self.assertEqual((status["state"], status["exit_code"]), ("exited", 7))
        self.assertIn("hi", Path(status["log"]).read_text())

    def test_start_keeps_job_records_out_of_git(self) -> None:
        self.start("quick", "true")
        self.assertEqual((self.ws / "work" / "pids" / ".gitignore").read_text(), "*\n")
        self.assertEqual(self.run_script("run_owned.py", "wait", "quick", "--timeout", "10").returncode, 0)

    def test_job_sees_its_name_and_group(self) -> None:
        job = self.start("env", "sh", "-c", 'echo "$RUN_OWNED_JOB $RUN_OWNED_PGID"')
        self.run_script("run_owned.py", "wait", "env", "--timeout", "10")
        log = Path(self.record("env")["log"]).read_text().strip()
        self.assertEqual(log, f"env {job['pgid']}")

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
            self.forge(stranger, lstart="Mon Jan  1 00:00:00 2001")
            out = self.run_script("run_owned.py", "stop", "forged")
            self.assertEqual(out.returncode, 3)
            self.assertIn("cannot confirm", out.stderr)
            self.assertIsNone(stranger.poll())
        finally:
            stranger.kill()
            stranger.wait()

    def test_refuses_a_record_without_a_start_time(self) -> None:
        stranger = subprocess.Popen(["sleep", "300"], start_new_session=True)
        try:
            self.forge(stranger, lstart=None)
            out = self.run_script("run_owned.py", "stop", "forged")
            self.assertEqual(out.returncode, 3)
            self.assertIn("cannot confirm", out.stderr)
            self.assertIsNone(stranger.poll(), "no getsid fallback reaches a process it cannot identify")
            status = json.loads(self.run_script("run_owned.py", "status", "forged").stdout)
            self.assertEqual(status["state"], "unverifiable")
        finally:
            stranger.kill()
            stranger.wait()

    def test_refuses_to_signal_when_ps_fails(self) -> None:
        job = self.start("owned", "sleep", "30")
        broken = self.root / "broken-ps"
        broken.mkdir()
        (broken / "ps").write_text("#!/bin/sh\nexit 1\n")
        (broken / "ps").chmod(0o755)
        out = self.run_script("run_owned.py", "stop", "owned", PATH=f"{broken}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(out.returncode, 3)
        self.assertIn("cannot confirm", out.stderr)
        self.assertTrue(_group_alive(job["pgid"]), "an unreadable start time is never treated as a match")
        self.assertEqual(self.run_script("run_owned.py", "stop", "owned").returncode, 0)
        self.assertFalse(_group_alive(job["pgid"]))

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


class TreeHashTest(TempWorkspace):
    def test_hash_covers_paths_and_contents_but_not_caches(self) -> None:
        (self.ws / "a").mkdir()
        (self.ws / "a" / "x.md").write_text("one")
        first = gate_common.tree_sha256(self.ws)
        (self.ws / "a" / "__pycache__").mkdir()
        (self.ws / "a" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
        (self.ws / ".DS_Store").write_bytes(b"\0")
        self.assertEqual(gate_common.tree_sha256(self.ws), first)
        (self.ws / "a" / "x.md").write_text("two")
        self.assertNotEqual(gate_common.tree_sha256(self.ws), first)
        (self.ws / "a" / "x.md").write_text("one")
        (self.ws / "a" / "x.md").rename(self.ws / "a" / "y.md")
        self.assertNotEqual(gate_common.tree_sha256(self.ws), first, "a rename changes the hash")


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
        record = self.evaluation("gate.json")
        self.assertEqual(record["mode"], "plugin")
        self.assertEqual(record["skill_sha256"], gate_common.skill_sha256(self.ws, "demo"))
        self.assertEqual(record["cases_sha256"], gate_common.cases_sha256(self.ws, self.CASES))
        self.assertEqual(self.record("gate")["cmd"][2], str(SCRIPTS / "gate.py"), "the gate ran as the owned job")
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)

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

    def test_unrecorded_eval_case_is_a_precondition_error(self) -> None:
        self.make_build(self.CASES)
        extra = self.ws / "work" / "evaluation" / "plugin" / "evals" / "extra"
        extra.mkdir()
        (extra / "prompt.md").write_text("x")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3)
        self.assertIn("present but unrecorded ['extra']", out.stdout)


class GateLifecycleTest(TempWorkspace):
    CASES = ["gold-01"]

    def detach(self, ws: Path | None = None) -> dict:
        out = self.run_script("gate.py", "--detach", "--model", "m", ws=ws, FAKE_MODE="hang")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("wait gate --timeout 540", out.stderr)
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
            self.assertIn("wait gate --timeout 540", out.stderr)
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
        sibling = subprocess.Popen(old_evals["cmd"], cwd=old_evals["cwd"], start_new_session=True,
                                   env=_env(self.bin, FAKE_MODE="hang"))
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
        self.assertIn("GATE VALID", Path(self.record("gate")["log"]).read_text())

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
        self.assertFalse((self.argv_log.read_text().count("--json")), "plugin eval never ran")
        self.assertIn("gold=2 mean gold delta=+0.500", out.stdout)

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

    def test_missing_adapter_is_a_precondition_error(self) -> None:
        self.make_build(["input-01"], invoked=False)
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 3)
        self.assertIn("run-case.sh is missing", out.stdout)


class CheckGateTest(TempWorkspace):
    def record_gate(self, cases: list[str], finished: bool = True, hashed: bool = True) -> None:
        record = {"mode": "manual", "skill": "demo", "cases": cases, "artifact": "work/evaluation/uplift.json",
                  "started": "2026-10-17T00:00:00+00:00", "finished": "2026-10-17T00:01:00+00:00" if finished else None}
        if hashed:
            record |= {"skill_sha256": gate_common.skill_sha256(self.ws, "demo"),
                       "cases_sha256": gate_common.cases_sha256(self.ws, cases)}
        (self.ws / "work" / "evaluation" / "gate.json").write_text(json.dumps(record))

    def write_uplift(self, cases: list[str], runs: int = 3, where: str = "work/evaluation", **extra: object) -> None:
        path = self.ws / where / "uplift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"cases": [{"case": c, "with": [1.0] * runs, "without": [0.0] * runs} for c in cases],
                "partial": False}
        path.write_text(json.dumps(data | extra))

    def gated(self, cases: list[str], **uplift: object) -> None:
        self.make_build(cases, invoked=False)
        self.record_gate(cases)
        self.write_uplift(cases, **uplift)

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
        self.record_gate(["input-01"])
        path = self.ws / "work" / "evaluation" / "uplift.json"
        path.write_text(json.dumps({"cases": [{"case": "input-01", "with": [1.0] * 3, "without": [0.0] * 3}]}))
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
        self.record_gate(["input-01", "input-02"])
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
        self.record_gate(["input-01"])
        path = self.ws / "work" / "evaluation" / "uplift.json"
        path.write_text(json.dumps({"cases": [{"case": "input-01", "with": [0.5] * 3, "without": [0.5] * 3}],
                                    "partial": False}))
        self.assertEqual(self.verdict()["non_positive"], ["input-01"])


if __name__ == "__main__":
    unittest.main()
