#!/usr/bin/env python3
"""Run background jobs as owned process groups, and stop only those groups (references/operating-rules.md).

    run_owned.py start <name> [--cwd DIR] [--log FILE] -- <cmd...>
    run_owned.py wait <name> [--timeout SECONDS]
    run_owned.py stop <name> [--grace SECONDS]      # waits up to grace + 10 s for the supervisor
    run_owned.py status <name>

A detached supervisor holds work/pids/<name>.lock while it lives, forks the command into a new session once the
record (<name>.json) is written, records its exit status (<name>.rc), and on `stop` (<name>.stop) TERMs the
group, KILLs it after the grace period and exits, so start refusal and stop need no `ps`. With the supervisor
gone, `stop` signals the recorded group only after `ps` confirms the leader's start time and that it leads the
group; otherwise it refuses, and `start` will not overwrite the record. The job sees RUN_OWNED_JOB,
RUN_OWNED_PGID and RUN_OWNED_TOKEN. Python 3.9+, macOS, Linux or WSL.

Exit codes: 0 success (wait: the job exited 0); the job's own exit code from wait; 2 usage error; 3 state
error (no record, name already running, a job it cannot verify or refuses to signal, an unconfirmed stop);
124 wait timed out (the job keeps running); 125 the job ended without an exit status (stopped or killed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    sys.exit("native Windows is not supported; run the factory inside WSL")
import fcntl  # noqa: E402

EXIT_STATE = 3
EXIT_TIMEOUT = 124
EXIT_NO_STATUS = 125
POLL_SECONDS = 0.2  # how often wait/stop re-read the exit status, lock and stop file
LOCK_RETRY_SECONDS = 1.0  # a live supervisor holds its lock for good; a second tells it from one exiting
START_LOCK_SECONDS = 30.0  # a start waits this long for a concurrent start of the same name
READY_SECONDS = 15.0  # for the supervisor to start Python and fork the command
GO_SECONDS = 30.0  # the forked command waits this long for `start` to write the record
STOP_MARGIN_SECONDS = 10.0  # beyond the grace, for the supervisor to KILL, reap and reply
SUPERVISE = "_supervise"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TERM, KILL = signal.SIGTERM, signal.SIGKILL


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
    token: str | None = None


def state_dir(workspace: Path) -> Path:
    return workspace / "work" / "pids"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file(sdir: Path, name: str, ext: str) -> Path:
    if not NAME_RE.match(name):
        raise OwnedError(f"invalid job name {name!r}: use letters, digits, '.', '_' or '-'")
    return sdir / f"{name}.{ext}"


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load(sdir: Path, name: str) -> Record:
    path = _file(sdir, name, "json")
    if not path.is_file():
        raise OwnedError(f"no record for job {name!r} at {path}; only jobs started by run_owned can be waited on or stopped")
    try:
        return Record(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError) as exc:
        raise OwnedError(f"unreadable record {path}: {exc}") from exc


def _lock(path: Path, wait: float) -> int | None:
    """An open fd holding path's exclusive flock, or None if another process still holds it after `wait` s."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.05)


def supervised(sdir: Path, name: str) -> bool:
    """True while the job's supervisor holds its lock, i.e. the job is live. Needs no `ps`."""
    if not _file(sdir, name, "lock").exists():
        return False
    fd = _lock(_file(sdir, name, "lock"), 0)
    if fd is not None:
        os.close(fd)
    return fd is None


def _lstart(pid: int) -> str | None:
    """The process's start time as an opaque string (`ps -o lstart`), or None when `ps` cannot say."""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _signal_group(pgid: int, sig: int) -> bool:
    """Signal the group; False when none of it can be signalled (macOS answers EPERM for a zombie-only group)."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _group_alive(pgid: int) -> bool:
    return _signal_group(pgid, 0)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _leader_state(rec: Record) -> str:
    """`ours` (the recorded leader still runs), `gone`, `reused` (its pid has another start time now) or
    `unknown` (alive, but no start time was recorded or can be read)."""
    if not _pid_alive(rec.pid):
        return "gone"
    now = _lstart(rec.pid) if rec.lstart is not None else None
    if now is None:
        return "unknown"
    return "ours" if now == rec.lstart else "reused"


def _leader_is_ours(rec: Record) -> bool | None:
    """True: the recorded leader runs; False: it is gone; None: unconfirmed, so it must not be signalled."""
    return {"ours": True, "gone": False}.get(_leader_state(rec))


def exit_status(rec: Record) -> int | None:
    try:
        return int(Path(rec.rc_file).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


# --- start and the supervisor ------------------------------------------------------------------------

def start(sdir: Path, name: str, cmd: list[str], cwd: Path, log: Path | None = None,
          env: dict[str, str] | None = None) -> subprocess.Popen:
    """Start cmd under a supervisor; return the supervisor's Popen (the caller may wait on it)."""
    if not cmd:
        raise OwnedError("no command given")
    path = _file(sdir, name, "json")
    sdir.mkdir(parents=True, exist_ok=True)
    if not (sdir / ".gitignore").is_file():
        (sdir / ".gitignore").write_text("*\n", encoding="utf-8")
    start_fd = _lock(_file(sdir, name, "start.lock"), START_LOCK_SECONDS)
    if start_fd is None:
        raise OwnedError(f"job {name!r} is already running or starting (another start holds its start lock); "
                         "stop it or pick another name")
    try:
        live = _lock(_file(sdir, name, "lock"), LOCK_RETRY_SECONDS)
        if live is None:
            raise OwnedError(f"job {name!r} is already running (its supervisor holds {_file(sdir, name, 'lock')}); "
                             "stop it or pick another name")
        os.close(live)
        if path.is_file():
            old = load(sdir, name)
            leader = _leader_state(old) if old.stopped is None and exit_status(old) is None else "gone"
            if leader == "ours":
                raise OwnedError(f"job {name!r} is already running (pgid {old.pgid}, supervisor gone); "
                                 "stop it or pick another name")
            if leader == "unknown":
                raise OwnedError(f"job {name!r} may still be running: its supervisor is gone and pid {old.pid} is "
                                 "alive, but no start time confirms it is the job (none recorded, or `ps` cannot "
                                 f"read one), so starting over its record would lose the group; once pid {old.pid} "
                                 f"is known not to be the job, delete {path}")
        return _spawn(sdir, name, cmd, cwd, log, env)
    finally:
        os.close(start_fd)


def _spawn(sdir: Path, name: str, cmd: list[str], cwd: Path, log: Path | None,
           env: dict[str, str] | None) -> subprocess.Popen:
    sdir = sdir.resolve()
    for ext in ("rc", "stop", "ready", "go"):
        _file(sdir, name, ext).unlink(missing_ok=True)
    log_path = (log or _file(sdir, name, "log")).resolve()
    token = secrets.token_hex(8)
    argv = [sys.executable, "-B", str(Path(__file__).resolve()), SUPERVISE, str(sdir), name, token, *cmd]
    with open(log_path, "wb") as log_fh:  # its own session, so it outlives the shell call that started it
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log_fh,
                                stderr=subprocess.STDOUT, start_new_session=True)
    ready, deadline = _file(sdir, name, "ready"), time.monotonic() + READY_SECONDS
    pid = None
    while pid is None and time.monotonic() < deadline and (proc.poll() is None or ready.exists()):
        pid = (_read_json(ready) or {}).get("pid")
        time.sleep(0.02)
    if not isinstance(pid, int):
        proc.kill()
        proc.wait()
        raise OwnedError(f"job {name!r}: its supervisor did not start the command; see {log_path}")
    rec = Record(name=name, pid=pid, pgid=pid, lstart=_lstart(pid), cmd=list(cmd), cwd=str(Path(cwd).resolve()),
                 log=str(log_path), rc_file=str(_file(sdir, name, "rc")), started=_now(), supervisor=proc.pid,
                 token=token)
    _write_json(_file(sdir, name, "json"), asdict(rec))
    _file(sdir, name, "go").write_text(token + "\n", encoding="utf-8")
    return proc


def _supervise(sdir: Path, name: str, token: str, cmd: list[str]) -> int:
    """Hold the name's lock, fork the command into its own session once the record exists, record its
    exit status, or on a stop request end its group; the lock goes with this process."""
    lock_fd = _lock(_file(sdir, name, "lock"), READY_SECONDS)
    if lock_fd is None:
        print(f"run_owned: job {name!r} is already running (its supervisor holds its lock)", file=sys.stderr)
        return EXIT_STATE
    stop_file, go = _file(sdir, name, "stop"), _file(sdir, name, "go")
    pid = os.fork()
    if pid == 0:  # the command: a new session, run once `start` has written the record
        os.setsid()
        os.close(lock_fd)
        deadline = time.monotonic() + GO_SECONDS
        while not go.exists():
            if time.monotonic() >= deadline:
                print(f"run_owned: job {name!r} was never recorded; the command did not run", file=sys.stderr)
                os._exit(EXIT_NO_STATUS)
            time.sleep(0.02)
        env = {**os.environ, "RUN_OWNED_JOB": name, "RUN_OWNED_PGID": str(os.getpid()), "RUN_OWNED_TOKEN": token}
        try:
            os.execvpe(cmd[0], cmd, env)
        except OSError as exc:
            print(f"run_owned: cannot run {cmd[0]!r}: {exc}", file=sys.stderr, flush=True)
        os._exit(127)
    signalled: list[int] = []
    for sig in (TERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda signum, _frame: signalled.append(signum))
    _write_json(_file(sdir, name, "ready"), {"pid": pid})
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            break
        if signalled or stop_file.exists():
            grace = float((_read_json(stop_file) or {}).get("grace", 10.0))
            _write_json(stop_file, {"grace": grace, "outcome": _terminate(pid, grace)})
            return 0
        time.sleep(POLL_SECONDS)
    # `stop` writes its request before anything is signalled: a command that ended under one was stopped.
    if not stop_file.exists():
        rc = os.waitstatus_to_exitcode(status)
        _file(sdir, name, "rc").write_text(f"{rc if rc >= 0 else 128 - rc}\n", encoding="utf-8")
    return 0


def _terminate(pid: int, grace: float) -> str:
    """TERM the command's group, KILL it after `grace`; the leader is ours and unreaped, so its pgid is too."""
    def alive() -> bool:
        try:
            os.waitpid(pid, os.WNOHANG)  # reap the leader so a zombie-only group reads as gone
        except ChildProcessError:
            pass
        return _group_alive(pid)

    _signal_group(pid, TERM)
    deadline = time.monotonic() + grace
    while alive() and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS / 2)
    outcome = "stopped"
    if alive():
        _signal_group(pid, KILL)
        outcome = "killed"
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    return outcome


# --- wait, stop, status ------------------------------------------------------------------------------

def wait(sdir: Path, name: str, timeout: float | None = None, proc: subprocess.Popen | None = None) -> int:
    """Wait for the job; return its exit code, EXIT_TIMEOUT, or EXIT_NO_STATUS."""
    rec = load(sdir, name)
    if proc is not None:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return EXIT_TIMEOUT
    deadline = None if timeout is None else time.monotonic() + timeout
    while exit_status(rec) is None and proc is None:
        # Without a supervisor, only a leader whose start time matches keeps the wait going.
        if not supervised(sdir, name) and _leader_is_ours(rec) is not True:
            break
        if deadline is not None and time.monotonic() >= deadline:
            return EXIT_TIMEOUT
        time.sleep(POLL_SECONDS)
    rc = exit_status(rec)
    return EXIT_NO_STATUS if rc is None else rc


def _target(sdir: Path, name: str) -> tuple[Record, str]:
    """(the record, how to stop it: `ended`, `supervised` or `signal`), refusing a group it cannot verify."""
    rec = load(sdir, name)
    if rec.pgid <= 1 or rec.pgid == os.getpgrp():
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: not a group run_owned created")
    if rec.stopped is not None or exit_status(rec) is not None:
        return rec, "ended"
    if supervised(sdir, name):
        return rec, "supervised"
    leader = _leader_is_ours(rec)
    if leader is None:
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: its supervisor is gone and it cannot confirm pid "
                         f"{rec.pid} is the job it recorded (no start time, `ps` unavailable, or a reused pid)")
    if leader and not _leads_its_group(rec):
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: the verified leader, pid {rec.pid}, does not lead "
                         "that group (the record was edited, or the leader moved groups)")
    return rec, "signal" if leader else "ended"


def _leads_its_group(rec: Record) -> bool:
    """The fallback signals rec.pgid once pid and start time match, so it must be the verified leader's group."""
    try:
        return rec.pgid == rec.pid == os.getpgid(rec.pid)
    except OSError:
        return False


def stop_many(sdir: Path, jobs: list[tuple[str, subprocess.Popen | None]], grace: float = 10.0) -> list[str]:
    """Stop every named job at once: a supervised one through its stop file; one whose supervisor is gone
    through TERM to its verified group, KILL after `grace`. Every name is checked before anything is signalled
    or written. Pass a job's supervisor Popen when this process started it, so it is reaped. Returns one
    outcome line per job; raises OwnedError if a stop was not confirmed."""
    targets = [(*_target(sdir, name), proc) for name, proc in jobs]
    for rec, how, _ in targets:
        if how != "ended":
            _write_json(_file(sdir, rec.name, "stop"), {"grace": grace})
        if how == "signal":
            _signal_group(rec.pgid, TERM)

    def done(rec: Record, how: str, proc: subprocess.Popen | None) -> bool:
        if proc is not None:
            proc.poll()
        return how == "ended" or not (supervised(sdir, rec.name) if how == "supervised" else _group_alive(rec.pgid))

    began, killed = time.monotonic(), set()
    while not all(done(*t) for t in targets) and time.monotonic() - began < grace + STOP_MARGIN_SECONDS:
        if time.monotonic() - began >= grace:
            for rec, how, _ in targets:
                if how == "signal" and rec.name not in killed and _group_alive(rec.pgid):
                    _signal_group(rec.pgid, KILL)
                    killed.add(rec.name)
        time.sleep(POLL_SECONDS)

    outcomes, unconfirmed = [], []
    for rec, how, proc in targets:
        if how == "ended":
            outcomes.append(f"job {rec.name!r} already ended")
            continue
        if not done(rec, how, proc):
            unconfirmed.append(rec.name)
            continue
        stop_file = _file(sdir, rec.name, "stop")
        was_killed = (rec.name in killed if how == "signal" else
                      (_read_json(stop_file) or {}).get("outcome") == "killed")
        stop_file.unlink(missing_ok=True)
        label = f"job {rec.name!r} (pgid {rec.pgid})"
        outcomes.append(f"{label} killed after {grace:g}s grace" if was_killed else f"{label} stopped")
        if proc is not None:
            proc.wait()
        rec.stopped = _now()
        _write_json(_file(sdir, rec.name, "json"), asdict(rec))
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
        return _supervise(Path(argv[1]), argv[2], argv[3], argv[4:])
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
    sub.add_parser("status", help="print the job's state as JSON").add_argument("name")
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
