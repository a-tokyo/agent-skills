#!/usr/bin/env python3
"""Run background jobs as owned process groups, and stop only those groups.

Each job runs in its own session and process group. Its record, work/pids/<name>.json, holds the
pid, pgid, the leader's start time, and the command. `stop` signals only a recorded group, after
checking that the leader's start time still matches the record, so it can never reach a sibling
build or the user's own processes the way `pkill`/`killall`/pattern kills do. When that identity
cannot be read (`ps` fails, or the record has none) it refuses to signal rather than guess.
`start` holds an exclusive flock on work/pids/<name>.lock from its state check through process
creation and the record, so concurrent starts of one name run it once and the rest are refused.
`start` keeps work/pids/ out of git (it writes a `.gitignore` of `*` there).

The job sees RUN_OWNED_JOB=<name> and RUN_OWNED_PGID=<its group id> in its environment.

    run_owned.py start <name> [--cwd DIR] [--log FILE] -- <cmd...>
    run_owned.py wait <name> [--timeout SECONDS]
    run_owned.py stop <name> [--grace SECONDS]
    run_owned.py status <name>

Paths are relative to --workspace (default: the current directory); records go to
<workspace>/work/pids/. Needs Python 3.9+ and `ps` (for the leader's start time). Tested on macOS
and Linux.

Exit codes: 0 success (wait: the job exited 0); the job's own exit code from wait; 2 usage error;
3 state error (no record, name already running, or a group this script refuses to signal);
124 wait timed out (the job keeps running); 125 the job ended without an exit status (killed).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

EXIT_STATE = 3
EXIT_TIMEOUT = 124
EXIT_NO_STATUS = 125
POLL_SECONDS = 0.2
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

# The wrapper shell is the group leader; it records the command's exit status atomically so a
# later `wait` from another process can read it (only a parent can waitpid).
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


def state_dir(workspace: Path) -> Path:
    return workspace / "work" / "pids"


def _check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise OwnedError(f"invalid job name {name!r}: use letters, digits, '.', '_' or '-'")


def _record_path(sdir: Path, name: str) -> Path:
    _check_name(name)
    return sdir / f"{name}.json"


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


def start(sdir: Path, name: str, cmd: list[str], cwd: Path, log: Path | None = None,
          env: dict[str, str] | None = None) -> subprocess.Popen:
    """Start cmd in a new session; return the Popen (the caller may wait on it directly)."""
    if not cmd:
        raise OwnedError("no command given")
    sdir.mkdir(parents=True, exist_ok=True)
    ignore = sdir / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("*\n")
    path = _record_path(sdir, name)
    with _name_lock(sdir, name):
        if path.is_file():
            old = load(sdir, name)
            if old.stopped is None and exit_status(old) is None and _leader_is_ours(old):
                raise OwnedError(f"job {name!r} is already running (pgid {old.pgid}); stop it or pick another name")
        return _spawn(sdir, name, path, cmd, cwd, log, env)


@contextmanager
def _name_lock(sdir: Path, name: str) -> Iterator[None]:
    """Serialize starts of one name: the state check, the spawn and the record are one step."""
    fd = os.open(sdir / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _spawn(sdir: Path, name: str, path: Path, cmd: list[str], cwd: Path, log: Path | None,
           env: dict[str, str] | None) -> subprocess.Popen:
    log_path = (log or sdir / f"{name}.log").resolve()
    rc_file = (sdir / f"{name}.rc").resolve()
    rc_file.unlink(missing_ok=True)
    with open(log_path, "wb") as log_fh:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", _WRAPPER, "run_owned", str(rc_file), name, *cmd],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    rec = Record(
        name=name, pid=proc.pid, pgid=os.getpgid(proc.pid), lstart=_lstart(proc.pid), cmd=list(cmd),
        cwd=str(Path(cwd).resolve()), log=str(log_path), rc_file=str(rc_file),
        started=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    _write_json(path, asdict(rec))
    return proc


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
        # Waiting signals nothing, so an unverifiable identity (None) keeps waiting; only a gone leader ends it.
        if _leader_is_ours(rec) is False:
            return EXIT_NO_STATUS
        if deadline is not None and time.monotonic() >= deadline:
            return EXIT_TIMEOUT
        time.sleep(POLL_SECONDS)


def _signalable(sdir: Path, name: str, proc: subprocess.Popen | None) -> Record:
    rec = load(sdir, name)
    if rec.pgid <= 1 or rec.pgid == os.getpgrp():
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: not a group run_owned created")
    # Holding the Popen proves the pid is ours (it cannot be reused before we reap it).
    leader = proc.poll() is None if proc is not None and proc.pid == rec.pid else _leader_is_ours(rec)
    if leader is None:
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: cannot confirm pid {rec.pid} is the job it recorded "
                         "(no start time, or it now belongs to another process)")
    return rec


def stop_many(sdir: Path, jobs: list[tuple[str, subprocess.Popen | None]], grace: float = 10.0) -> list[str]:
    """TERM every recorded group at once, then KILL the survivors after `grace` seconds. Each name is
    checked before any group is signalled. Pass a job's Popen when this process started it, so its
    leader is reaped rather than left a zombie. Returns one outcome line per job."""
    targets = [(_signalable(sdir, name, proc), proc) for name, proc in jobs]
    # With a leader gone, any process left in its group descends from it: a new group with that id
    # needs a new leader whose pid equals it, which _leader_is_ours would have seen.
    live = [rec for rec, _ in targets if _group_alive(rec.pgid)]
    for rec in live:
        _signal_group(rec.pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while any(_group_alive(rec.pgid) for rec in live) and time.monotonic() < deadline:
        for _, proc in targets:
            if proc is not None:
                proc.poll()
        time.sleep(POLL_SECONDS)
    outcomes = []
    for rec, proc in targets:
        if rec not in live:
            outcomes.append(f"job {rec.name!r} already ended")
        elif _group_alive(rec.pgid):
            _signal_group(rec.pgid, signal.SIGKILL)
            outcomes.append(f"job {rec.name!r} (pgid {rec.pgid}) killed after {grace:g}s grace")
        else:
            outcomes.append(f"job {rec.name!r} (pgid {rec.pgid}) stopped")
        if proc is not None:
            proc.wait()
        rec.stopped = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write_json(_record_path(sdir, rec.name), asdict(rec))
    return outcomes


def stop(sdir: Path, name: str, grace: float = 10.0, proc: subprocess.Popen | None = None) -> str:
    """TERM the recorded group, then KILL after `grace` seconds. Returns a one-line outcome."""
    return stop_many(sdir, [(name, proc)], grace)[0]


def status(sdir: Path, name: str) -> dict:
    rec = load(sdir, name)
    rc = exit_status(rec)
    if rc is not None:
        state = "exited"
    elif rec.stopped is not None:
        state = "stopped"
    else:
        state = {True: "running", False: "gone", None: "unverifiable"}[_leader_is_ours(rec)]
    return {"name": name, "pid": rec.pid, "pgid": rec.pgid, "state": state, "exit_code": rc, "log": rec.log}


def main(argv: list[str] | None = None) -> int:
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
    p_stop = sub.add_parser("stop", help="TERM the job's process group, KILL after the grace period")
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
            proc = start(sdir, args.name, cmd, cwd=(args.cwd or workspace), log=args.log)
            if load(sdir, args.name).lstart is None:
                print(f"run_owned: warning: `ps` could not read job {args.name!r}'s start time, so `stop` will "
                      "refuse to signal it", file=sys.stderr)
            print(json.dumps({"name": args.name, "pid": proc.pid, "pgid": proc.pid}))
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
