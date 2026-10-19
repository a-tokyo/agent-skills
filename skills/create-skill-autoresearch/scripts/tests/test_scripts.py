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
    """Fake `claude plugin eval`; behaviour from FAKE_MODE: complete | partial | hang | fewruns | silent |
    nodelta (gold cases report a null delta). Cases named trigout-* get skill-fired plus an output regex
    that passes in both arms (delta 0, the k-* task-2 Sonnet 5 trigger-2 shape); trigger-* only skill-fired,
    near-miss* only a not-fired one (max 0), holdout-* the worked case's approval regex plus skill-fired.
    A case directory holding a file named INVALID fails to load: like the real CLI, a `✗` line names it
    and the run skips it. `--max-cost-usd 0` loads the cases and stops before any run (exit 2)."""
    import json, os, sys, time
    from pathlib import Path
    args = sys.argv[1:]
    log = os.environ.get("FAKE_ARGV_LOG")
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps(args) + "\\n")
    if args[:3] == ["plugin", "eval", "--help"]:
        sys.exit(int(os.environ.get("FAKE_HELP_RC", "0")))
    invalid = sorted(p.name for p in Path("evals").iterdir() if (p / "INVALID").is_file())
    for name in invalid:
        print(f"✗ {Path('evals', name, 'case.yaml').resolve()}: invalid case.yaml:   graders.0: Unrecognized key")
    if invalid:
        print(f"{len(invalid)} case file(s) failed to load")
    if "--max-cost-usd" in args:
        sys.exit(2)
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
        if name.startswith("trigout"):
            graders = [{"name": "skill-fired", "type": "tool_used", "config": {"tool": "Skill"}},
                       {"name": "valid", "type": "regex", "config": {}}]
            fired = {"score": 1, "error": None, "graders": [{"name": "skill-fired", "passed": True},
                                                             {"name": "valid", "passed": True}]}
            bare = {"score": 1, "error": None, "graders": [{"name": "valid", "passed": True}]}
            return {"name": name, "graders": graders, "arms": {"with": [fired] * runs, "without": [bare] * runs},
                    "aggregates": {"delta": 0}}
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
        delta = None if mode == "nodelta" else 0.5
        if name.startswith("holdout"):
            graders = [{"name": "approval", "type": "regex",
                        "config": {"target": {"source": "file", "path": "po.json"}, "pattern": "director"}},
                       {"name": "skill-fired", "type": "tool_used", "config": {"tool": "Skill"}}]
            good = {"score": 1, "error": None, "graders": [{"name": "approval", "passed": True},
                                                            {"name": "skill-fired", "passed": True}]}
            bare = {"score": 0.6, "error": None, "graders": [{"name": "approval", "passed": False}]}
            return {"name": name, "graders": graders, "arms": {"with": [good] * runs, "without": [bare] * runs},
                    "aggregates": {"delta": None if mode == "nodelta" else 0.4}}
        grader = {"name": "fact", "type": "regex", "config": {}}
        good = {"score": 1, "error": None, "graders": [{"name": "fact", "passed": True}]}
        bare = {"score": 0.5, "error": None, "graders": [{"name": "fact", "passed": False}]}
        return {"name": name, "graders": [grader], "arms": {"with": [good] * runs, "without": [bare] * runs},
                "aggregates": {"delta": delta}}

    cases = sorted(p.name for p in Path("evals").iterdir() if (p / "prompt.md").is_file() and p.name not in invalid)
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


def _path_without_claude() -> str:
    dirs = [d for d in os.environ["PATH"].split(os.pathsep) if d and not os.path.exists(os.path.join(d, "claude"))]
    return os.pathsep.join(dirs or ["/usr/bin", "/bin"])


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

    def make_build(self, cases: list[str], invoked: bool = True, ws: Path | None = None,
                   training: str = "[]", manual: bool | None = None) -> None:
        """A build whose suite holds `cases`. Manual builds (default: user-invoked skills) also list them
        as gold standards under all_cases, the manual protocol's case source."""
        ws = ws or self.ws
        skill = ws / "output" / "demo"
        skill.mkdir(parents=True)
        flag = "" if invoked else "disable-model-invocation: true\n"
        (skill / "SKILL.md").write_text(f"---\nname: demo\n{flag}description: Demo.\n---\n# Demo\n")
        evaluation = ws / "work" / "evaluation"
        evaluation.mkdir(parents=True)
        listed = "\n".join(f'  - "{c}"' for c in cases)
        gold = f"all_cases:\n{listed}\n" if (not invoked if manual is None else manual) and cases else ""
        (evaluation / "data-split.yaml").write_text(
            f"{gold}training: {training}\nplugin_eval_available: 0  # probe exit code\nplugin_eval:\n{listed}\n")
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
                   no_claude: bool = False, **env: str) -> subprocess.CompletedProcess:
        """no_claude: PATH holds no `claude` at all (neither the fake nor a real install)."""
        full = _env(self.bin, FAKE_ARGV_LOG=str(self.argv_log), **env)
        if no_claude:
            full["PATH"] = _path_without_claude()
        return subprocess.run([*_script(name), "--workspace", str(ws or self.ws), *args], capture_output=True,
                              text=True, timeout=timeout, env=full)

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

    def test_works_end_to_end_with_ps_denied(self) -> None:
        denied = self.root / "denied-ps"
        denied.mkdir()
        (denied / "ps").write_text("#!/bin/sh\necho 'ps: operation not permitted' >&2\nexit 1\n")
        (denied / "ps").chmod(0o755)
        no_ps = {"PATH": f"{denied}{os.pathsep}{self.bin}{os.pathsep}{os.environ['PATH']}"}
        sibling = subprocess.Popen(["sleep", "300"], start_new_session=True)
        try:
            out = subprocess.run([*_script("run_owned.py"), "--workspace", str(self.ws), "start", "boxed", "--",
                                  "sh", "-c", "sleep 300 & sleep 300; wait"],
                                 capture_output=True, text=True, timeout=30, env={**_env(self.bin), **no_ps})
            self.assertEqual(out.returncode, 0, out.stderr)
            job = json.loads(out.stdout)
            self.assertIsNone(self.record("boxed")["lstart"], "ps really was unavailable")
            status = json.loads(self.run_script("run_owned.py", "status", "boxed", **no_ps).stdout)
            self.assertEqual(status["state"], "running")
            again = self.run_script("run_owned.py", "start", "boxed", "--", "sleep", "30", **no_ps)
            self.assertEqual(again.returncode, 3, "a duplicate start is refused without ps")
            self.assertIn("already running", again.stderr)
            self.assertEqual(self.run_script("run_owned.py", "wait", "boxed", "--timeout", "0.3", **no_ps).returncode,
                             124)
            stopped = self.run_script("run_owned.py", "stop", "boxed", "--grace", "5", **no_ps)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertFalse(_group_alive(job["pgid"]), "the supervisor stopped its own group")
            self.assertIsNone(sibling.poll(), "a sibling with the same command line survives")
            status = json.loads(self.run_script("run_owned.py", "status", "boxed", **no_ps).stdout)
            self.assertEqual(status["state"], "stopped")
            self.assertEqual(self.run_script("run_owned.py", "wait", "boxed", **no_ps).returncode, 125)
        finally:
            sibling.kill()
            sibling.wait()

    def test_stop_signals_a_verified_group_whose_supervisor_is_gone(self) -> None:
        job = self.start("orphan", "sleep", "300")
        if self.record("orphan")["lstart"] is None:
            self.run_script("run_owned.py", "stop", "orphan")
            self.skipTest("the signal fallback needs `ps` to confirm the leader")
        supervisor = self.record("orphan")["supervisor"]
        os.kill(supervisor, 9)
        self.assertTrue(_until(lambda: not _alive(supervisor)), "init reaps the killed supervisor")
        self.assertTrue(_group_alive(job["pgid"]), "the job outlives its supervisor")
        self.assertEqual(json.loads(self.run_script("run_owned.py", "status", "orphan").stdout)["state"], "running")
        self.assertEqual(self.run_script("run_owned.py", "start", "orphan", "--", "true").returncode, 3)
        out = self.run_script("run_owned.py", "stop", "orphan", "--grace", "2")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(_group_alive(job["pgid"]))

    def test_concurrent_starts_run_the_job_once(self) -> None:
        argv = [*_script("run_owned.py"), "--workspace", str(self.ws), "start", "race", "--", "sleep", "30"]
        racers = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=_env(self.bin)) for _ in range(6)]
        results = [(p.wait(timeout=60), *p.communicate()) for p in racers]
        winners = [json.loads(out) for rc, out, _ in results if rc == 0]
        self.assertEqual(len(winners), 1, results)
        self.assertTrue(all(rc == 3 and "already running" in err for rc, _, err in results if rc != 0), results)
        self.assertEqual(self.record("race")["pid"], winners[0]["pid"], "the record is the winner's")
        self.assertEqual(self.run_script("run_owned.py", "stop", "race").returncode, 0)
        self.assertFalse(_group_alive(winners[0]["pgid"]))

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

    def test_double_quoted_names_are_json_decoded(self) -> None:
        self.assertEqual(self.read('plugin_eval: ["gold-\\u00e9", "a\\"b", "plain"]\n').plugin_eval,
                         ["gold-é", 'a"b', "plain"])
        self.assertEqual(self.read("plugin_eval:\n  - 'it''s'\n  - \"x\\\\y\"\n").plugin_eval, ["it''s", "x\\y"])

    def test_non_ascii_case_names_round_trip_through_write_and_read(self) -> None:
        path = self.ws / "data-split.yaml"
        cases = ["gold-é", "naïve-02", "日本-03", "plain-04"]
        gate_common.write_gate_cases(path, cases, 0, "plugin/evals")
        self.assertIn('"gold-\\u00e9"', path.read_text(), "written as JSON escapes")
        self.assertEqual(gate_common.read_data_split(path).plugin_eval, cases)

    def test_gold_standards_are_recorded_by_any_split_key(self) -> None:
        self.assertFalse(self.read("training: []\nplugin_eval: [a]\n").gold_recorded)
        self.assertTrue(self.read("test: [input/g1.md]\nplugin_eval: [a]\n").gold_recorded)
        self.assertTrue(self.read("all_cases:\n  - input/g1.md\nplugin_eval: [a]\n").gold_recorded)


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

    def test_unloadable_case_stops_the_gate_before_it_runs(self) -> None:
        self.make_build(self.CASES)
        (self.ws / "work" / "evaluation" / "plugin" / "evals" / "gold-02" / "INVALID").write_text("")
        out = self.run_script("gate.py", "--model", "m")
        self.assertEqual(out.returncode, 3)
        self.assertIn("1 case file(s) fail to load", out.stdout)
        self.assertIn("gold-02/case.yaml: invalid case.yaml", out.stdout)
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
        self.assertIn("trigger-1/case.yaml", out.stderr)

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

    def test_manual_record_prefers_the_test_split(self) -> None:
        self.make_build(["gold-01"], invoked=False)
        path = self.ws / "work" / "evaluation" / "data-split.yaml"
        path.write_text("training: [input/a.md, input/b.md]\nvalidation: [input/c.md]\ntest: [input/d.md]\n")
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        self.assertEqual(self.split().plugin_eval, ["input/d.md"])
        path.write_text("all_cases: [input/a.md, input/b.md]\ntraining: [input/b.md, input/c.md]\ntest: []\n")
        self.assertEqual(self.run_script("gate.py", "record").returncode, 0)
        self.assertEqual(self.split().plugin_eval, ["input/a.md", "input/b.md", "input/c.md"],
                         "3-9 band: every gold standard, the held-out one included")

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
        self.assertIn("claude", os.listdir(self.bin), "the fake exists, and no_claude must keep it off PATH")
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
        self.assertEqual(sorted(record["inputs_sha256"]), ["input/po-01.json", "work/evaluation/score.py"])
        self.assertEqual(record["artifact_sha256"],
                         gate_common.file_sha256(self.ws / "work" / "evaluation" / "uplift.json"))

    def assert_stale(self, changed: str) -> None:
        verdict = json.loads(self.run_script("check_gate.py", "--json").stdout)
        self.assertEqual(verdict["code"], "stale_inputs")
        self.assertTrue(verdict["reason"].startswith("gate inputs changed since the gate; re-run 5.0"))
        self.assertIn(changed, verdict["reason"])

    def test_scratch_files_after_the_gate_are_not_inputs(self) -> None:
        self.manual_gate()
        evaluation = self.ws / "work" / "evaluation"
        for scratch in (".cases.tmp", "check_gate.out", "notes.log", "uplift.prev.json"):
            (evaluation / scratch).write_text("x")
        self.assertEqual(self.run_script("check_gate.py").returncode, 0)

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

    def test_missing_adapter_is_a_precondition_error(self) -> None:
        self.make_build(["input-01"], invoked=False)
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 3)
        self.assertIn("run-case.sh is missing", out.stdout)


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

    def test_ship_needs_a_valid_gate_and_its_line(self) -> None:
        self.make_build(["gold-01", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        self.benchmark("**Verdict: SHIP_WITH_CAVEATS**\n")
        self.assertIn("lacks check_gate.py's GATE VALID line", self.finish()["reason"])
        self.benchmark(f"**Verdict: SHIP_WITH_CAVEATS**\n\n{self.gate_line()}\n")
        self.assertEqual(self.finish()["verdict"], "SHIP WITH CAVEATS")
        self.assertTrue(self.finish()["finished"])
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Edited.\n---\n")
        data = self.finish()
        self.assertFalse(data["finished"])
        self.assertIn("skill changed since the gate", data["reason"])

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


class CheckGateTest(TempWorkspace):
    def record_gate(self, cases: list[str], finished: bool = True, hashed: bool = True) -> None:
        record = {"mode": "manual", "skill": "demo", "cases": cases, "artifact": "work/evaluation/uplift.json",
                  "started": "2026-10-17T00:00:00+00:00", "finished": "2026-10-17T00:01:00+00:00" if finished else None}
        if hashed:
            evaluation = self.ws / "work" / "evaluation"
            record |= {"skill_sha256": gate_common.skill_sha256(self.ws, "demo"),
                       "cases_sha256": gate_common.cases_sha256(self.ws, cases),
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
