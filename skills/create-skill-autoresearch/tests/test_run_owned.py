"""Offline tests for run_owned.py: owned jobs, their supervisor, start refusal and stop.

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

from helpers import (DETACHED, IGNORES_TERM, NATIVE_WINDOWS, SCRIPTS, SLEEP, TREE, TRUE, TempWorkspace, _alive, _env,
                     _group_alive, _script, _until, as_native_windows, py)
import run_owned  # noqa: E402  (on sys.path through helpers)


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

    def test_a_command_that_ends_after_a_stop_request_records_no_exit_status(self) -> None:
        """The race behind the flaky ps-denied test: the group TERM can end the command before the supervisor
        acts on the stop request, which then recorded an exit status and turned "stopped" into "exited"."""
        stop = self.ws / "work" / "pids" / "job.stop"
        self.start("job", *py(f"open({str(stop)!r}, 'w').write('{{}}'); raise SystemExit(143)"))
        self.assertEqual(self.run_script("run_owned.py", "wait", "job", "--timeout", "10").returncode, 125)
        self.assertFalse((self.ws / "work" / "pids" / "job.rc").exists(), "a stopped job's status is the stop")
        self.start("job", *py("raise SystemExit(7)"))
        self.assertEqual(self.run_script("run_owned.py", "wait", "job", "--timeout", "10").returncode, 7,
                         "without a stop request the exit status is recorded")

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
            os.kill(supervisor, 9)
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

    def test_native_windows_exits_early_with_one_message(self) -> None:
        out = as_native_windows(SCRIPTS / "run_owned.py")
        self.assertEqual((out.returncode, out.stderr.strip()), (1, NATIVE_WINDOWS))

    def test_refuses_to_start_a_running_name_twice(self) -> None:
        self.start("once", *SLEEP)
        out = self.run_script("run_owned.py", "start", "once", "--", *SLEEP)
        self.assertEqual(out.returncode, 3)
        self.assertIn("already running", out.stderr)
        self.run_script("run_owned.py", "stop", "once")


if __name__ == "__main__":
    unittest.main()
