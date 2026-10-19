"""Offline tests for run_owned.py, gate.py and check_gate.py.

A fake `claude` on PATH stands in for Claude Code, fake run-case.sh adapters stand in for the
build's executor, and every workspace is a temp dir. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/scripts/tests -v
"""

from __future__ import annotations

import json
import locale
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import gate  # noqa: E402
import gate_common  # noqa: E402
import run_owned  # noqa: E402

WINDOWS = os.name == "nt"
PY = sys.executable


def py(code: str) -> list[str]:
    return [PY, "-c", code]


SLEEP = py("import time; time.sleep(300)")
TRUE = py("pass")
# The job and a child it starts; the child's pid goes to the log so a test can check the whole tree ended.
TREE = py("import subprocess, sys, time\n"
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
          "print('child', child.pid, flush=True)\ntime.sleep(300)")
IGNORES_TERM = py("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)")
DETACHED = {} if WINDOWS else {"start_new_session": True}

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
    sys.stdout.reconfigure(encoding="utf-8")
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
    """CLAUDECODE=1 as inside Claude Code, where the gate defaults to plugin eval; pass CLAUDECODE="" for
    another host."""
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "PYTHONDONTWRITEBYTECODE": "1", "CLAUDECODE": "1", **extra}


def _path_without_claude() -> str:
    dirs = [d for d in os.environ["PATH"].split(os.pathsep)
            if d and not any(os.path.exists(os.path.join(d, "claude" + ext)) for ext in ("", ".cmd", ".exe", ".bat"))]
    return os.pathsep.join(dirs or ["/usr/bin", "/bin"])


def _script(name: str) -> list[str]:
    return [sys.executable, "-B", str(SCRIPTS / name)]


def _alive(pid: int) -> bool:
    return run_owned._pid_alive(pid)


def _group_alive(pgid: int) -> bool:
    """macOS/Linux: any member of the group; Windows (no groups): the leader."""
    if WINDOWS:
        return run_owned._pid_alive(pgid)
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _until(predicate, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


class TempWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        # Windows cannot delete a file a just-stopped process still holds; a leftover temp dir is harmless.
        self._tmp = tempfile.TemporaryDirectory(**({"ignore_cleanup_errors": True} if WINDOWS else {}))
        self.root = Path(self._tmp.name)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / ("claude.py" if WINDOWS else "claude")
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        if WINDOWS:
            (self.bin / "claude.cmd").write_text(f'@"{PY}" "%~dp0claude.py" %*\r\n')
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
            f"{gold}training: {training}\nplugin_eval_available: 0  # probe exit code\nplugin_eval:\n{listed}\n",
            encoding="utf-8")
        plugin = evaluation / "plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "demo-eval", "version": "0.0.0"}')
        for case in cases:
            (plugin / "evals" / case).mkdir(parents=True)
            (plugin / "evals" / case / "prompt.md").write_text(f"Do {case}.\n", encoding="utf-8")

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
                              encoding="utf-8", errors="replace", timeout=timeout, env=full)

    def evaluation(self, name: str) -> dict:
        return json.loads((self.ws / "work" / "evaluation" / name).read_text(encoding="utf-8"))

    def record(self, name: str, ws: Path | None = None) -> dict:
        return json.loads(((ws or self.ws) / "work" / "pids" / f"{name}.json").read_text(encoding="utf-8"))


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
            "cmd": SLEEP, "cwd": str(self.ws), "log": str(pids / "forged.log"),
            "rc_file": str(pids / "forged.rc"), "started": "2001-01-01T00:00:00+00:00", "stopped": None}))

    def test_wait_returns_the_job_exit_code(self) -> None:
        self.start("seven", *py("print('hi'); raise SystemExit(7)"))
        out = self.run_script("run_owned.py", "wait", "seven", "--timeout", "10")
        self.assertEqual(out.returncode, 7)
        status = json.loads(self.run_script("run_owned.py", "status", "seven").stdout)
        self.assertEqual((status["state"], status["exit_code"]), ("exited", 7))
        self.assertIn("hi", Path(status["log"]).read_text(encoding="utf-8"))

    def test_start_keeps_job_records_out_of_git(self) -> None:
        self.start("quick", *TRUE)
        self.assertEqual((self.ws / "work" / "pids" / ".gitignore").read_text(), "*\n")
        self.assertEqual(self.run_script("run_owned.py", "wait", "quick", "--timeout", "10").returncode, 0)

    def test_job_sees_its_name_and_group(self) -> None:
        job = self.start("env", *py("import os; print(os.environ['RUN_OWNED_JOB'], os.environ['RUN_OWNED_PGID'])"))
        self.run_script("run_owned.py", "wait", "env", "--timeout", "10")
        log = Path(self.record("env")["log"]).read_text().strip()
        self.assertEqual(log, f"env {job['pgid']}")

    def child_pid(self, name: str) -> int:
        log = Path(self.record(name)["log"])
        self.assertTrue(_until(lambda: "child " in log.read_text(encoding="utf-8")), "the job started its child")
        return int(log.read_text(encoding="utf-8").split("child ", 1)[1].split()[0])

    def test_stop_kills_only_its_own_group(self) -> None:
        sibling = subprocess.Popen(SLEEP, **DETACHED)
        try:
            job = self.start("mine", *TREE)
            child = self.child_pid("mine")
            self.assertTrue(_group_alive(job["pgid"]))
            out = self.run_script("run_owned.py", "stop", "mine", "--grace", "5")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertFalse(_group_alive(job["pgid"]), "every process in the owned group is gone")
            self.assertTrue(_until(lambda: not _alive(child)), "and so is the child the job started")
            self.assertIsNone(sibling.poll(), "a sibling with the same command line survives")
            status = json.loads(self.run_script("run_owned.py", "status", "mine").stdout)
            self.assertEqual(status["state"], "stopped")
        finally:
            sibling.kill()
            sibling.wait()

    def test_wait_timeout_leaves_the_job_running(self) -> None:
        job = self.start("slow", *SLEEP)
        out = self.run_script("run_owned.py", "wait", "slow", "--timeout", "0.5")
        self.assertEqual(out.returncode, 124)
        self.assertIn("still running", out.stderr)
        self.assertTrue(_alive(job["pid"]))
        self.assertEqual(self.run_script("run_owned.py", "stop", "slow").returncode, 0)
        self.assertFalse(_group_alive(job["pgid"]))

    def test_term_ignoring_job_is_killed_after_grace(self) -> None:
        job = self.start("stubborn", *IGNORES_TERM)
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
        stranger = subprocess.Popen(SLEEP, **DETACHED)
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
        stranger = subprocess.Popen(SLEEP, **DETACHED)
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

    @unittest.skipIf(WINDOWS, "`ps` plays no part on Windows (GetProcessTimes)")
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

    def test_start_refuses_to_overwrite_a_live_unverifiable_job(self) -> None:
        stranger = subprocess.Popen(SLEEP, **DETACHED)
        try:
            self.forge(stranger, lstart=None)
            before = self.record("forged")
            out = self.run_script("run_owned.py", "start", "forged", "--", *TRUE)
            self.assertEqual(out.returncode, 3, out.stderr)
            self.assertIn("may still be running", out.stderr)
            self.assertEqual(self.record("forged"), before, "the only record of the live group is kept")
            self.assertIsNone(stranger.poll())
        finally:
            stranger.kill()
            stranger.wait()

    def test_start_over_a_reused_pid_proceeds(self) -> None:
        stranger = subprocess.Popen(SLEEP, **DETACHED)
        try:
            self.forge(stranger, lstart="Mon Jan  1 00:00:00 2001")
            if run_owned._lstart(stranger.pid) is None:
                self.skipTest("needs `ps` to read the stranger's start time")
            job = self.start("forged", *SLEEP)
            self.assertNotEqual(job["pid"], stranger.pid)
            self.assertEqual(self.run_script("run_owned.py", "stop", "forged").returncode, 0)
            self.assertIsNone(stranger.poll(), "the process that reused the pid is untouched")
        finally:
            stranger.kill()
            stranger.wait()

    @unittest.skipIf(WINDOWS, "`ps` plays no part on Windows (GetProcessTimes); the forged record covers it")
    def test_start_refuses_when_the_supervisor_died_and_ps_is_denied(self) -> None:
        denied = self.root / "denied-ps"
        denied.mkdir()
        (denied / "ps").write_text("#!/bin/sh\necho 'ps: operation not permitted' >&2\nexit 1\n")
        (denied / "ps").chmod(0o755)
        no_ps = {"PATH": f"{denied}{os.pathsep}{self.bin}{os.pathsep}{os.environ['PATH']}"}
        out = self.run_script("run_owned.py", "start", "boxed", "--", "sleep", "300", **no_ps)
        self.assertEqual(out.returncode, 0, out.stderr)
        job = json.loads(out.stdout)
        try:
            supervisor = self.record("boxed")["supervisor"]
            os.kill(supervisor, 9)
            self.assertTrue(_until(lambda: not _alive(supervisor)))
            self.assertTrue(_group_alive(job["pgid"]), "the job outlives its supervisor")
            again = self.run_script("run_owned.py", "start", "boxed", "--", *TRUE, **no_ps)
            self.assertEqual(again.returncode, 3, again.stderr)
            self.assertIn("may still be running", again.stderr)
            self.assertEqual(self.record("boxed")["pid"], job["pid"], "the live group stays recorded")
        finally:
            os.killpg(job["pgid"], 9)

    def test_stop_signals_a_verified_group_whose_supervisor_is_gone(self) -> None:
        job = self.start("orphan", *SLEEP)
        if self.record("orphan")["lstart"] is None:
            self.run_script("run_owned.py", "stop", "orphan")
            self.skipTest("the signal fallback needs `ps` to confirm the leader")
        supervisor = self.record("orphan")["supervisor"]
        os.kill(supervisor, 9)
        self.assertTrue(_until(lambda: not _alive(supervisor)), "the killed supervisor is gone")
        self.assertTrue(_group_alive(job["pgid"]), "the job outlives its supervisor")
        self.assertEqual(json.loads(self.run_script("run_owned.py", "status", "orphan").stdout)["state"], "running")
        self.assertEqual(self.run_script("run_owned.py", "start", "orphan", "--", *TRUE).returncode, 3)
        out = self.run_script("run_owned.py", "stop", "orphan", "--grace", "2")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(_group_alive(job["pgid"]))

    def test_fallback_refuses_a_pgid_its_verified_leader_does_not_lead(self) -> None:
        stranger = subprocess.Popen(SLEEP, **DETACHED)
        job = self.start("orphan", *SLEEP)
        try:
            if self.record("orphan")["lstart"] is None:
                self.skipTest("the signal fallback needs `ps` to confirm the leader")
            supervisor = self.record("orphan")["supervisor"]
            os.kill(supervisor, signal.SIGTERM if WINDOWS else 9)
            self.assertTrue(_until(lambda: not _alive(supervisor)))
            path = self.ws / "work" / "pids" / "orphan.json"
            path.write_text(json.dumps(self.record("orphan") | {"pgid": stranger.pid}))
            out = self.run_script("run_owned.py", "stop", "orphan", "--grace", "1")
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn(f"pid {job['pid']}, does not lead that group", out.stderr)
            self.assertIsNone(stranger.poll(), "the group the edited record names is untouched")
            self.assertTrue(_alive(job["pid"]))
        finally:
            run_owned._signal_group(job["pgid"], run_owned.KILL)
            stranger.kill()
            stranger.wait()

    def test_concurrent_starts_run_the_job_once(self) -> None:
        argv = [*_script("run_owned.py"), "--workspace", str(self.ws), "start", "race", "--", *SLEEP]
        racers = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                                   env=_env(self.bin)) for _ in range(6)]
        results = [(p.wait(timeout=60), *p.communicate()) for p in racers]
        winners = [json.loads(out) for rc, out, _ in results if rc == 0]
        self.assertEqual(len(winners), 1, results)
        self.assertTrue(all(rc == 3 and "already running" in err for rc, _, err in results if rc != 0), results)
        self.assertEqual(self.record("race")["pid"], winners[0]["pid"], "the record is the winner's")
        self.assertEqual(self.run_script("run_owned.py", "stop", "race").returncode, 0)
        self.assertFalse(_group_alive(winners[0]["pgid"]))

    def test_refuses_to_start_a_running_name_twice(self) -> None:
        self.start("once", *SLEEP)
        out = self.run_script("run_owned.py", "start", "once", "--", *SLEEP)
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
        gate_common.write_gate_cases(path, cases, 0, "plugin/evals", "probe exit code")
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
        self.assertEqual(out.returncode, 3, "4 gold standards have no test split (SKILL.md 4.2)")
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

    def test_backend_manual_inside_claude_code_never_calls_claude(self) -> None:
        self.make_build(["gold-01"], invoked=True, manual=True)
        self.write_adapter()
        out = self.run_script("gate.py", "--backend", "manual", "--model", "m", "--executor", "agentic")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.evaluation("gate.json")["mode"], "manual")
        self.assertFalse(self.argv_log.exists())

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
                          "plugin/evals/results/run-1/report.html"):
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

    def test_missing_adapter_is_a_precondition_error(self) -> None:
        self.make_build(["input-01"], invoked=False)
        out = self.run_script("gate.py", "--model", "m", "--executor", "single-call")
        self.assertEqual(out.returncode, 3)
        self.assertIn("run-case.sh is missing", out.stdout)


class GateWindowsTest(TempWorkspace):
    """The Windows branches gate.py takes that need no Windows to check."""

    def test_adapter_runs_through_git_bash_on_windows(self) -> None:
        adapter = (self.ws / "work" / "evaluation" / "run-case.sh").resolve()
        with mock.patch.object(gate.os, "name", "nt"), mock.patch.object(gate, "_git_bash", return_value="C:/Git/bin/bash.exe"):
            self.assertEqual(gate._adapter_argv(self.ws), ["C:/Git/bin/bash.exe", adapter.as_posix()])
        with mock.patch.object(gate.os, "name", "nt"), mock.patch.object(gate, "_git_bash", return_value=None):
            with self.assertRaisesRegex(gate_common.GateError, "Git for Windows"):
                gate._adapter_argv(self.ws)

    def test_git_bash_skips_the_wsl_launcher(self) -> None:
        system32 = self.root / "Windows" / "System32"
        git = self.root / "Program Files" / "Git" / "bin"
        for folder in (system32, git):
            folder.mkdir(parents=True)
            (folder / "bash.exe").write_text("")
        env = {"SystemRoot": str(self.root / "Windows"), "CLAUDE_CODE_GIT_BASH_PATH": str(system32 / "bash.exe"),
               "ProgramFiles": str(self.root / "Program Files"), "PATH": str(self.root / "empty")}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(gate._git_bash(), str(git / "bash.exe"))

    def test_the_gate_stops_when_its_stop_file_appears(self) -> None:
        stop_file = self.root / "gate.stop"
        with mock.patch.dict(os.environ, {"RUN_OWNED_STOP_FILE": str(stop_file)}):
            gate._STOP.clear()
            try:
                gate._watch_stop_file()
                self.assertFalse(gate._STOP.wait(0.6))
                stop_file.write_text("{}")
                self.assertTrue(gate._STOP.wait(5), "the stop file is the request on Windows")
            finally:
                gate._STOP.set()
                time.sleep(gate.POLL_SECONDS * 2)
                gate._STOP.clear()

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

    def test_ship_needs_a_valid_gate_and_its_line(self) -> None:
        self.make_build(["gold-01", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
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
        self.assertIn("needs the panel receipt work/experiments/panel.json", self.finish()["reason"])
        self.assertEqual(self.run_script("check_gate.py", "--panel").returncode, 0)
        self.assertTrue(self.finish()["finished"])
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Edited.\n---\n")
        data = self.finish()
        self.assertFalse(data["finished"])
        self.assertIn("skill changed since the gate", data["reason"])

    def test_ship_on_a_panel_that_scored_an_earlier_skill_is_unfinished(self) -> None:
        self.make_build(["gold-01", "trigger-1"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        panel = self.run_script("check_gate.py", "--panel")
        self.assertEqual(panel.returncode, 0, panel.stdout + panel.stderr)
        self.assertIn("PANEL RECEIPT: work/experiments/panel.json", panel.stderr)
        (self.ws / "output" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Fixed after the panel.\n---\n")
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0, "the re-run gate validates")
        self.benchmark(f"Verdict: SHIP\n\n{self.gate_line()}\n")
        data = self.finish()
        self.assertFalse(data["finished"], "the r-t2-factory-v030-haiku-5 shape: fixed after the panel, re-gated")
        self.assertIn("the panel scored an earlier output/demo/", data["reason"])
        self.assertEqual(self.run_script("check_gate.py", "--panel").returncode, 0)
        self.assertTrue(self.finish()["finished"])

    def test_plain_ship_with_a_gold_delta_at_or_below_zero_is_unfinished(self) -> None:
        self.make_build(["gold-01", "trigout-2"])
        self.assertEqual(self.run_script("gate.py", "--model", "m").returncode, 0)
        self.assertEqual(self.run_script("check_gate.py", "--panel").returncode, 0)
        self.benchmark(f"Verdict: SHIP\n\n{self.gate_line()}\n")
        self.assertIn("gold delta<=0 on trigout-2 caps the verdict at SHIP WITH CAVEATS", self.finish()["reason"])
        self.benchmark(f"Verdict: SHIP WITH CAVEATS\n\n{self.gate_line()}\n")
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
