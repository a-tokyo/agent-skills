#!/usr/bin/env python3
"""Run background jobs as owned process groups, and stop only those groups.

Each job runs under a small supervisor (this script, in its own session) that starts the command in
a new session and process group and watches work/pids/<name>.stop. `stop` writes that file and waits;
the supervisor TERMs only its own child's group, KILLs it after the grace period, and exits. No
process identity from outside is needed, so stop works where `ps` is missing or denied (agent
sandboxes), and it can never reach a sibling build or the user's own processes the way
`pkill`/`killall`/pattern kills do.

The supervisor holds an exclusive flock on work/pids/<name>.lock for as long as it runs: a held lock
is a live job. `start` takes that lock before it checks state and hands it to the supervisor, so the
check, the spawn and the record are one step: concurrent starts of one name run it once, and a start
while the job is live is refused. Only if the supervisor is gone while the job's leader still runs
does `stop` fall back to signalling the recorded group, and only after `ps` confirms the leader's
start time matches the record; when it cannot (no `ps`, no start time, a reused pid) it refuses.

The record, work/pids/<name>.json, holds the pids, the pgid, the leader's start time (when `ps` can
read it) and the command. The job sees RUN_OWNED_JOB=<name> and RUN_OWNED_PGID=<its group id> in
its environment. `start` keeps work/pids/ out of git (it writes a `.gitignore` of `*` there).

    run_owned.py start <name> [--cwd DIR] [--log FILE] -- <cmd...>
    run_owned.py wait <name> [--timeout SECONDS]
    run_owned.py stop <name> [--grace SECONDS]      # waits up to grace + 10 s for the supervisor
    run_owned.py status <name>

Paths are relative to --workspace (default: the current directory); records go to
<workspace>/work/pids/. Needs Python 3.9+ on macOS or Linux (flock, sessions); `ps` is optional.

Exit codes: 0 success (wait: the job exited 0); the job's own exit code from wait; 2 usage error;
3 state error (no record, name already running, a group this script refuses to signal, or a
supervisor that did not confirm the stop); 124 wait timed out (the job keeps running); 125 the job
ended without an exit status (killed).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

EXIT_STATE = 3
EXIT_TIMEOUT = 124
EXIT_NO_STATUS = 125
POLL_SECONDS = 0.2
LOCK_RETRY_SECONDS = 1.0
READY_SECONDS = 15.0
STOP_MARGIN_SECONDS = 10.0
SUPERVISE = "_supervise"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

# The wrapper shell is the job's group leader; it records the command's exit status atomically so a
# later `wait` from any process can read it.
_WRAPPER = ('rc_file=$1; RUN_OWNED_JOB=$2; RUN_OWNED_PGID=$$; export RUN_OWNED_JOB RUN_OWNED_PGID; shift 2; '
            '"$@"; rc=$?; printf "%s\\n" "$rc" > "$rc_file.tmp" && mv "$rc_file.tmp" "$rc_file"; exit "$rc"')


class OwnedError(Exception):
    """A state error: missing record, name in use, or a group we refuse to signal."""


@dataclass
class Record:
    name: str
    pid: int
    pgid: int
    lstart: str | None
    cmd: list[str]
    cwd: str
    log: str
    rc_file: str
    started: str
    stopped: str | None = None
    supervisor: int | None = None


def state_dir(workspace: Path) -> Path:
    return workspace / "work" / "pids"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise OwnedError(f"invalid job name {name!r}: use letters, digits, '.', '_' or '-'")


def _record_path(sdir: Path, name: str) -> Path:
    _check_name(name)
    return sdir / f"{name}.json"


def _lock_path(sdir: Path, name: str) -> Path:
    return sdir / f"{name}.lock"


def _stop_path(sdir: Path, name: str) -> Path:
    return sdir / f"{name}.stop"


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def load(sdir: Path, name: str) -> Record:
    path = _record_path(sdir, name)
    if not path.is_file():
        raise OwnedError(f"no record for job {name!r} at {path}; only jobs started by run_owned can be waited on or stopped")
    try:
        return Record(**json.loads(path.read_text()))
    except (json.JSONDecodeError, TypeError) as exc:
        raise OwnedError(f"unreadable record {path}: {exc}") from exc


def supervised(sdir: Path, name: str) -> bool:
    """True while the job's supervisor holds its lock, i.e. the job is live. Needs no `ps`."""
    try:
        fd = os.open(_lock_path(sdir, name), os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def _lstart(pid: int) -> str | None:
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip()
    return value or None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _group_alive(pgid: int) -> bool:
    """True while the group has a member we can signal. macOS answers EPERM for a group whose
    only members are zombies, so EPERM counts as nothing left to stop."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _leader_is_ours(rec: Record) -> bool | None:
    """True if the recorded leader is still running, False if it is gone, None if its identity
    cannot be confirmed (its pid now belongs to another process, the record holds no start time,
    or `ps` cannot read one) and it must not be signalled."""
    if not _pid_alive(rec.pid):
        return False
    if rec.lstart is None:
        return None
    return True if _lstart(rec.pid) == rec.lstart else None


def exit_status(rec: Record) -> int | None:
    try:
        return int(Path(rec.rc_file).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _acquire(sdir: Path, name: str) -> int:
    """The name's lock, taken before any state check. A brief retry rides out a status probe."""
    fd = os.open(_lock_path(sdir, name), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + LOCK_RETRY_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise OwnedError(f"job {name!r} is already running (its supervisor holds "
                                 f"{_lock_path(sdir, name)}); stop it or pick another name") from None
            time.sleep(0.05)


def start(sdir: Path, name: str, cmd: list[str], cwd: Path, log: Path | None = None,
          env: dict[str, str] | None = None) -> subprocess.Popen:
    """Start cmd under a supervisor; return the supervisor's Popen (the caller may wait on it)."""
    if not cmd:
        raise OwnedError("no command given")
    path = _record_path(sdir, name)
    sdir.mkdir(parents=True, exist_ok=True)
    ignore = sdir / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("*\n")
    lock_fd = _acquire(sdir, name)
    try:
        if path.is_file():
            old = load(sdir, name)
            if old.stopped is None and exit_status(old) is None and _leader_is_ours(old):
                raise OwnedError(f"job {name!r} is already running (pgid {old.pgid}, supervisor gone); "
                                 "stop it or pick another name")
        return _spawn(sdir, name, path, cmd, cwd, log, env, lock_fd)
    finally:
        # Closing never unlocks: the supervisor shares this open file description and keeps it.
        os.close(lock_fd)


def _spawn(sdir: Path, name: str, path: Path, cmd: list[str], cwd: Path, log: Path | None,
           env: dict[str, str] | None, lock_fd: int) -> subprocess.Popen:
    log_path = (log or sdir / f"{name}.log").resolve()
    rc_file = (sdir / f"{name}.rc").resolve()
    stop_file = _stop_path(sdir, name).resolve()
    rc_file.unlink(missing_ok=True)
    stop_file.unlink(missing_ok=True)
    ready_r, ready_w = os.pipe()
    try:
        with open(log_path, "wb") as log_fh:
            proc = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve()), SUPERVISE, str(ready_w), str(rc_file),
                 name, str(stop_file), *cmd],
                cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(lock_fd, ready_w),
            )
        os.close(ready_w)
        ready_w = -1
        pid = _read_ready(ready_r)
    finally:
        os.close(ready_r)
        if ready_w != -1:
            os.close(ready_w)
    if pid is None:
        proc.kill()
        proc.wait()
        raise OwnedError(f"job {name!r}: its supervisor did not start the command; see {log_path}")
    rec = Record(
        name=name, pid=pid, pgid=pid, lstart=_lstart(pid), cmd=list(cmd), cwd=str(Path(cwd).resolve()),
        log=str(log_path), rc_file=str(rc_file), started=_now(), supervisor=proc.pid,
    )
    _write_json(path, asdict(rec))
    return proc


def _read_ready(fd: int) -> int | None:
    data = b""
    deadline = time.monotonic() + READY_SECONDS
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            return None
        chunk = os.read(fd, 64)
        if not chunk:
            return None
        data += chunk
    try:
        return int(data.split(b"\n", 1)[0])
    except ValueError:
        return None


def _supervise(argv: list[str]) -> int:
    """The supervisor: run the wrapper in its own group, stop that group on request, then exit.
    It inherits the name's lock and holds it until it exits."""
    ready_fd, rc_file, name, stop_file, cmd = int(argv[0]), argv[1], argv[2], Path(argv[3]), argv[4:]
    signalled: list[int] = []
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda signum, _frame: signalled.append(signum))
    child = subprocess.Popen(["/bin/sh", "-c", _WRAPPER, "run_owned", rc_file, name, *cmd],
                             stdin=subprocess.DEVNULL, start_new_session=True)
    os.write(ready_fd, f"{child.pid}\n".encode())
    os.close(ready_fd)
    while child.poll() is None:
        if signalled or stop_file.exists():
            grace = _requested_grace(stop_file)
            outcome = _terminate(child, grace)
            _write_json(stop_file, {"grace": grace, "outcome": outcome})
            return 0
        time.sleep(POLL_SECONDS)
    return 0


def _requested_grace(stop_file: Path) -> float:
    try:
        return float(json.loads(stop_file.read_text())["grace"])
    except (OSError, ValueError, KeyError, TypeError):
        return 10.0


def _terminate(child: subprocess.Popen, grace: float) -> str:
    """TERM the child's group, KILL it after `grace`; the child is ours (unreaped), so its pgid is too."""
    _signal_group(child.pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while _group_alive(child.pid) and time.monotonic() < deadline:
        child.poll()
        time.sleep(POLL_SECONDS / 2)
    outcome = "stopped"
    if _group_alive(child.pid):
        _signal_group(child.pid, signal.SIGKILL)
        outcome = "killed"
    child.wait()
    return outcome


def wait(sdir: Path, name: str, timeout: float | None = None, proc: subprocess.Popen | None = None) -> int:
    """Wait for the job; return its exit code, EXIT_TIMEOUT, or EXIT_NO_STATUS."""
    rec = load(sdir, name)
    if proc is not None:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return EXIT_TIMEOUT
        rc = exit_status(rec)
        return EXIT_NO_STATUS if rc is None else rc
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        rc = exit_status(rec)
        if rc is not None:
            return rc
        # Without a supervisor, only a leader `ps` confirms keeps the wait going.
        if not supervised(sdir, name) and _leader_is_ours(rec) is not True:
            rc = exit_status(rec)
            return EXIT_NO_STATUS if rc is None else rc
        if deadline is not None and time.monotonic() >= deadline:
            return EXIT_TIMEOUT
        time.sleep(POLL_SECONDS)


@dataclass
class _Target:
    rec: Record
    proc: subprocess.Popen | None
    how: str  # "ended" | "supervised" | "signal"


def _target(sdir: Path, name: str, proc: subprocess.Popen | None) -> _Target:
    rec = load(sdir, name)
    if rec.pgid <= 1 or rec.pgid == os.getpgrp():
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: not a group run_owned created")
    if rec.stopped is not None or exit_status(rec) is not None:
        return _Target(rec, proc, "ended")
    if supervised(sdir, name):
        return _Target(rec, proc, "supervised")
    leader = _leader_is_ours(rec)
    if leader is None:
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: its supervisor is gone and it cannot confirm pid "
                         f"{rec.pid} is the job it recorded (no start time, `ps` unavailable, or a reused pid)")
    return _Target(rec, proc, "signal" if leader else "ended")


def stop_many(sdir: Path, jobs: list[tuple[str, subprocess.Popen | None]], grace: float = 10.0) -> list[str]:
    """Stop every named job at once: a supervised job through its stop file, a job whose supervisor
    is gone by TERM to its verified group, KILL after `grace`. Each name is checked before anything
    is signalled or written. Pass a job's Popen (its supervisor) when this process started it, so it
    is reaped. Returns one outcome line per job; raises OwnedError if a supervisor did not confirm."""
    targets = [_target(sdir, name, proc) for name, proc in jobs]
    for t in targets:
        if t.how == "supervised":
            _write_json(_stop_path(sdir, t.rec.name), {"grace": grace})
        elif t.how == "signal":
            _signal_group(t.rec.pgid, signal.SIGTERM)

    def done(t: _Target) -> bool:
        if t.proc is not None:
            t.proc.poll()
        if t.how == "supervised":
            return not supervised(sdir, t.rec.name)
        return t.how == "ended" or not _group_alive(t.rec.pgid)

    start_time = time.monotonic()
    killed: set[str] = set()
    while not all(done(t) for t in targets) and time.monotonic() - start_time < grace + STOP_MARGIN_SECONDS:
        if time.monotonic() - start_time >= grace:
            for t in targets:
                if t.how == "signal" and t.rec.name not in killed and _group_alive(t.rec.pgid):
                    _signal_group(t.rec.pgid, signal.SIGKILL)
                    killed.add(t.rec.name)
        time.sleep(POLL_SECONDS)

    outcomes, unconfirmed = [], []
    for t in targets:
        label = f"job {t.rec.name!r} (pgid {t.rec.pgid})"
        if t.how == "ended":
            outcomes.append(f"job {t.rec.name!r} already ended")
            continue
        if not done(t):
            unconfirmed.append(t.rec.name)
            continue
        if t.how == "supervised":
            stop_file = _stop_path(sdir, t.rec.name)
            reply = json.loads(stop_file.read_text()) if stop_file.is_file() else {}
            stop_file.unlink(missing_ok=True)
            outcome = reply.get("outcome")
        else:
            outcome = "killed" if t.rec.name in killed else "stopped"
        outcomes.append(f"{label} killed after {grace:g}s grace" if outcome == "killed" else f"{label} stopped")
        if t.proc is not None:
            t.proc.wait()
        t.rec.stopped = _now()
        _write_json(_record_path(sdir, t.rec.name), asdict(t.rec))
    if unconfirmed:
        raise OwnedError(f"{'; '.join(outcomes) + '; ' if outcomes else ''}no confirmed stop for {unconfirmed} "
                         f"within {grace + STOP_MARGIN_SECONDS:g}s")
    return outcomes


def stop(sdir: Path, name: str, grace: float = 10.0, proc: subprocess.Popen | None = None) -> str:
    """Stop the job (TERM, then KILL after `grace` seconds). Returns a one-line outcome."""
    return stop_many(sdir, [(name, proc)], grace)[0]


def status(sdir: Path, name: str) -> dict:
    rec = load(sdir, name)
    rc = exit_status(rec)
    if rc is not None:
        state = "exited"
    elif rec.stopped is not None:
        state = "stopped"
    elif supervised(sdir, name):
        state = "running"
    else:
        state = {True: "running", False: "gone", None: "unverifiable"}[_leader_is_ours(rec)]
    return {"name": name, "pid": rec.pid, "pgid": rec.pgid, "state": state, "exit_code": rc, "log": rec.log}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == [SUPERVISE]:
        return _supervise(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path("."), help="build workspace root (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser("start", help="start <cmd> as an owned process group")
    p_start.add_argument("name")
    p_start.add_argument("--cwd", type=Path, help="working directory for the job (default: the workspace)")
    p_start.add_argument("--log", type=Path, help="stdout+stderr file (default: work/pids/<name>.log)")
    p_start.add_argument("cmd", nargs=argparse.REMAINDER, help="-- followed by the command")
    p_wait = sub.add_parser("wait", help="wait for the job and exit with its exit code")
    p_wait.add_argument("name")
    p_wait.add_argument("--timeout", type=float, help="seconds; exit 124 if the job is still running")
    p_stop = sub.add_parser("stop", help="stop the job's process group: TERM, KILL after the grace period")
    p_stop.add_argument("name")
    p_stop.add_argument("--grace", type=float, default=10.0, help="seconds between TERM and KILL (default 10)")
    p_status = sub.add_parser("status", help="print the job's state as JSON")
    p_status.add_argument("name")
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    sdir = state_dir(workspace)
    try:
        if args.command == "start":
            cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
            start(sdir, args.name, cmd, cwd=(args.cwd or workspace), log=args.log)
            rec = load(sdir, args.name)
            print(json.dumps({"name": args.name, "pid": rec.pid, "pgid": rec.pgid}))
            return 0
        if args.command == "wait":
            rc = wait(sdir, args.name, timeout=args.timeout)
            if rc == EXIT_TIMEOUT:
                print(f"run_owned: job {args.name!r} still running after {args.timeout:g}s", file=sys.stderr)
            elif rc == EXIT_NO_STATUS:
                print(f"run_owned: job {args.name!r} ended without an exit status (killed?)", file=sys.stderr)
            return rc
        if args.command == "stop":
            print(stop(sdir, args.name, grace=args.grace))
            return 0
        print(json.dumps(status(sdir, args.name)))
        return 0
    except OwnedError as exc:
        print(f"run_owned: {exc}", file=sys.stderr)
        return EXIT_STATE


if __name__ == "__main__":
    sys.exit(main())
