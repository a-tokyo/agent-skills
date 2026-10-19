#!/usr/bin/env python3
"""Run background jobs as owned process trees, and stop only those trees.

Each job runs under a small supervisor (this script) that starts a wrapper (this script again); the
wrapper runs the command and records its exit status atomically, so a later `wait` from any process
can read it. On macOS and Linux the wrapper leads a new session and process group; on Windows the
supervisor puts it in a Job Object, which every process it starts joins. The supervisor watches
work/pids/<name>.stop: `stop` writes that file and waits; the supervisor ends only its own job and
exits. macOS/Linux: TERM to the group, KILL after the grace period. Windows has no TERM: the job may
exit on its own when the stop file (its path is in $RUN_OWNED_STOP_FILE) appears, and the Job Object
is terminated after the grace period. No process identity from outside is needed, so stop works where
`ps` is missing or denied (agent sandboxes), and it can never reach a sibling build or the user's own
processes the way `pkill`/`killall`/`taskkill /IM` and other pattern kills do.

The supervisor holds an exclusive lock on work/pids/<name>.lock for as long as it runs: a held lock is
a live job. `start` holds work/pids/<name>.start.lock from its state check until the record is
written, so concurrent starts of one name run it once and a start while the job is live is refused;
the wrapper runs the command only once the record exists. Only if the supervisor is gone while the
job's leader still runs does `stop` fall back to the recorded leader (macOS/Linux: its group; Windows:
`taskkill /PID <leader> /T`), and only after its start time (`ps` or GetProcessTimes) matches the
record; when it cannot (no start time, a reused pid) it refuses, and `start` refuses to overwrite it.

The record, work/pids/<name>.json, holds the pids, the pgid (the leader's pid on Windows), the leader's
start time, a token and the command. The job sees RUN_OWNED_JOB=<name>, RUN_OWNED_PGID=<the leader's
pid>, RUN_OWNED_TOKEN=<the record's token> and RUN_OWNED_STOP_FILE in its environment. `start` keeps
work/pids/ out of git (it writes a `.gitignore` of `*` there).

    run_owned.py start <name> [--cwd DIR] [--log FILE] -- <cmd...>
    run_owned.py wait <name> [--timeout SECONDS]
    run_owned.py stop <name> [--grace SECONDS]      # waits up to grace + 10 s for the supervisor
    run_owned.py status <name>

Paths are relative to --workspace (default: the current directory); records go to
<workspace>/work/pids/. Needs Python 3.9+ on macOS, Linux or Windows (stdlib only; `ps` is optional).

Exit codes: 0 success (wait: the job exited 0); the job's own exit code from wait; 2 usage error;
3 state error (no record, name already running, a job this script cannot verify or refuses to
signal, or a supervisor that did not confirm the stop); 124 wait timed out (the job keeps running);
125 the job ended without an exit status (killed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = os.name == "nt"
if WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl

EXIT_STATE = 3
EXIT_TIMEOUT = 124
EXIT_NO_STATUS = 125
# How often wait/stop re-read the exit status, lock and stop file: prompt, and cheap on CPU.
POLL_SECONDS = 0.2
# A live supervisor holds its lock for good, so a second tells a live job from one that just exited.
LOCK_RETRY_SECONDS = 1.0
# A start waits this long for a concurrent start of the same name to finish, then sees its live job.
START_LOCK_SECONDS = 30.0
# Time for the supervisor to start Python and the wrapper and report its pid; a cold Python start on
# Windows (antivirus scanning a new process) can take several seconds.
READY_SECONDS = 15.0
# The wrapper waits this long for `start` to write the record before giving up on the command.
GO_SECONDS = 30.0
# Beyond the grace, time for the supervisor to KILL, reap and write its reply before stop gives up.
STOP_MARGIN_SECONDS = 10.0
SUPERVISE = "_supervise"
WRAP = "_wrap"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TERM = signal.SIGTERM
KILL = getattr(signal, "SIGKILL", 9)


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


def _replace(src: Path, dst: Path) -> None:
    """os.replace; on Windows a reader holding dst open makes it fail for a moment, so retry briefly
    (50 x 20 ms: a second for the reader to let go)."""
    for _ in range(50):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not WINDOWS:
                raise
            time.sleep(0.02)
    os.replace(src, dst)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace(tmp, path)


def _write_json(path: Path, data: dict) -> None:
    _write_text(path, json.dumps(data, indent=2) + "\n")


def load(sdir: Path, name: str) -> Record:
    path = _record_path(sdir, name)
    if not path.is_file():
        raise OwnedError(f"no record for job {name!r} at {path}; only jobs started by run_owned can be waited on or stopped")
    try:
        return Record(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError) as exc:
        raise OwnedError(f"unreadable record {path}: {exc}") from exc


# --- locks: flock on macOS/Linux, msvcrt.locking on Windows; both end with the process ---------------

def _try_lock(fd: int) -> bool:
    if WINDOWS:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _lock(path: Path, wait: float) -> int | None:
    """An open fd holding path's exclusive lock, or None if another process holds it after `wait` s."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + wait
    while not _try_lock(fd):
        if time.monotonic() >= deadline:
            os.close(fd)
            return None
        time.sleep(0.05)  # 20 tries a second: a lock frees the moment its holder exits
    return fd


def supervised(sdir: Path, name: str) -> bool:
    """True while the job's supervisor holds its lock, i.e. the job is live. Needs no `ps`."""
    try:
        fd = os.open(_lock_path(sdir, name), os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        return not _try_lock(fd)
    finally:
        os.close(fd)


# --- process identity and signals --------------------------------------------------------------------

if WINDOWS:
    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _K32.OpenProcess.restype = wintypes.HANDLE
    _K32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _K32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _K32.WaitForSingleObject.restype = wintypes.DWORD
    _K32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _K32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    _K32.CreateJobObjectW.restype = wintypes.HANDLE
    _K32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _K32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _K32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _K32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD))
    _QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    _SYNCHRONIZE = 0x00100000
    _WAIT_TIMEOUT = 0x102
    _ACCESS_DENIED = 5
    _JOB_ACCOUNTING = 1  # JobObjectBasicAccountingInformation

    class _Accounting(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


def _lstart(pid: int) -> str | None:
    """The process's start time as an opaque string (`ps -o lstart`, or the creation FILETIME on Windows)."""
    if WINDOWS:
        handle = _K32.OpenProcess(_QUERY, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _K32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            _K32.CloseHandle(handle)
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=10)  # `ps -p` answers at once; ten seconds bounds a stalled one
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip()
    return value or None


def _pid_alive(pid: int) -> bool:
    if WINDOWS:
        # Never os.kill(pid, 0) here: on Windows it terminates the process.
        handle = _K32.OpenProcess(_SYNCHRONIZE | _QUERY, False, pid)
        if not handle:
            return ctypes.get_last_error() == _ACCESS_DENIED
        try:
            return _K32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            _K32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _group_alive(pgid: int) -> bool:
    """True while the group has a member we can signal. macOS answers EPERM for a group whose
    only members are zombies, so EPERM counts as nothing left to stop. Windows: the leader runs."""
    if WINDOWS:
        return _pid_alive(pgid)
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _kill_tree(pid: int) -> None:
    """Windows only: force-end the process and its descendants by PID (never by name)."""
    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
    try:
        subprocess.run([str(taskkill), "/PID", str(pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=60)  # /T walks the whole tree; a minute bounds a stalled one
    except (OSError, subprocess.TimeoutExpired):
        pass


def _signal_group(pgid: int, sig: int) -> None:
    if WINDOWS:
        if sig == KILL:
            _kill_tree(pgid)
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class _Job:
    """A Windows Job Object holding the wrapper and everything it starts."""

    def __init__(self) -> None:
        self.handle = _K32.CreateJobObjectW(None, None) or None

    def assign(self, proc: subprocess.Popen) -> bool:
        return bool(self.handle) and bool(_K32.AssignProcessToJobObject(self.handle, int(proc._handle)))

    def active(self) -> int:
        info = _Accounting()
        if not _K32.QueryInformationJobObject(self.handle, _JOB_ACCOUNTING, ctypes.byref(info),
                                              ctypes.sizeof(info), None):
            return 0
        return info.ActiveProcesses

    def terminate(self) -> None:
        _K32.TerminateJobObject(self.handle, 1)


def _leader_state(rec: Record) -> str:
    """`ours` (the recorded leader still runs), `gone`, `reused` (its pid now belongs to a process with
    another start time) or `unknown` (the pid is alive but the record holds no start time, or none can
    be read)."""
    if not _pid_alive(rec.pid):
        return "gone"
    if rec.lstart is None:
        return "unknown"
    now = _lstart(rec.pid)
    if now is None:
        return "unknown"
    return "ours" if now == rec.lstart else "reused"


def _leader_is_ours(rec: Record) -> bool | None:
    """True if the recorded leader is still running, False if it is gone, None if its identity
    cannot be confirmed (a reused pid, no start time, or `ps` unavailable) and it must not be signalled."""
    return {"ours": True, "gone": False}.get(_leader_state(rec))


def exit_status(rec: Record) -> int | None:
    try:
        return int(Path(rec.rc_file).read_text(encoding="utf-8").strip())
    except (FileNotFoundError, PermissionError, ValueError):
        return None


# --- start -------------------------------------------------------------------------------------------

def start(sdir: Path, name: str, cmd: list[str], cwd: Path, log: Path | None = None,
          env: dict[str, str] | None = None) -> subprocess.Popen:
    """Start cmd under a supervisor; return the supervisor's Popen (the caller may wait on it)."""
    if not cmd:
        raise OwnedError("no command given")
    path = _record_path(sdir, name)
    sdir.mkdir(parents=True, exist_ok=True)
    ignore = sdir / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("*\n", encoding="utf-8")
    start_fd = _lock(sdir / f"{name}.start.lock", START_LOCK_SECONDS)
    if start_fd is None:
        raise OwnedError(f"job {name!r} is already running or starting (another start holds "
                         f"{sdir / (name + '.start.lock')}); stop it or pick another name")
    try:
        live = _lock(_lock_path(sdir, name), LOCK_RETRY_SECONDS)
        if live is None:
            raise OwnedError(f"job {name!r} is already running (its supervisor holds "
                             f"{_lock_path(sdir, name)}); stop it or pick another name")
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
        return _spawn(sdir, name, path, cmd, cwd, log, env)
    finally:
        os.close(start_fd)


def _detached(argv: list[str], cwd: Path, env: dict[str, str] | None, log_fh) -> subprocess.Popen:
    """The supervisor, outside the caller's session (macOS/Linux) or console and job (Windows), so it
    outlives the shell call that started it."""
    common = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT)
    if not WINDOWS:
        return subprocess.Popen(argv, start_new_session=True, **common)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(argv, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **common)
    except OSError:
        # The caller's job forbids breakaway; the supervisor stays in it.
        return subprocess.Popen(argv, creationflags=flags, **common)


def _spawn(sdir: Path, name: str, path: Path, cmd: list[str], cwd: Path, log: Path | None,
           env: dict[str, str] | None) -> subprocess.Popen:
    log_path = (log or sdir / f"{name}.log").resolve()
    rc_file = (sdir / f"{name}.rc").resolve()
    stop_file = _stop_path(sdir, name).resolve()
    ready_file = (sdir / f"{name}.ready").resolve()
    go_file = (sdir / f"{name}.go").resolve()
    for stale in (rc_file, stop_file, ready_file, go_file):
        stale.unlink(missing_ok=True)
    token = secrets.token_hex(8)
    argv = [sys.executable, "-B", str(Path(__file__).resolve()), SUPERVISE, str(ready_file), str(rc_file), name,
            str(stop_file), str(go_file), token, str(_lock_path(sdir, name).resolve()), *cmd]
    with open(log_path, "wb") as log_fh:
        proc = _detached(argv, cwd, env, log_fh)
    pid = _await_ready(ready_file, proc)
    if pid is None:
        proc.kill()
        proc.wait()
        raise OwnedError(f"job {name!r}: its supervisor did not start the command; see {log_path}")
    rec = Record(
        name=name, pid=pid, pgid=pid, lstart=_lstart(pid), cmd=list(cmd), cwd=str(Path(cwd).resolve()),
        log=str(log_path), rc_file=str(rc_file), started=_now(), supervisor=proc.pid, token=token,
    )
    _write_json(path, asdict(rec))
    _write_text(go_file, token + "\n")
    return proc


def _await_ready(ready_file: Path, proc: subprocess.Popen) -> int | None:
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        try:
            return int(json.loads(ready_file.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if proc.poll() is not None and not ready_file.exists():
            return None
        time.sleep(0.02)  # the handshake takes well under a second; a short poll keeps start fast
    return None


# --- the supervisor and the wrapper (internal subcommands) ------------------------------------------

def _supervise(argv: list[str]) -> int:
    """The supervisor: take the name's lock, run the wrapper in its own group (Windows: Job Object),
    stop it on request, then exit. The lock is held until this process exits."""
    ready_file, rc_file, name, stop_file, go_file, token, lock_path = argv[:7]
    cmd, stop_file = argv[7:], Path(stop_file)
    lock_fd = _lock(Path(lock_path), READY_SECONDS)
    if lock_fd is None:
        print(f"run_owned: job {name!r} is already running (its supervisor holds {lock_path})", file=sys.stderr)
        return EXIT_STATE
    signalled: list[int] = []
    for sig in (TERM, getattr(signal, "SIGHUP", None), signal.SIGINT, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, lambda signum, _frame: signalled.append(signum))
    wrapper = [sys.executable, "-B", str(Path(__file__).resolve()), WRAP, rc_file, name, token, go_file,
               str(stop_file), *cmd]
    job = None
    if WINDOWS:
        child = subprocess.Popen(wrapper, stdin=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        job = _Job()
        if not job.assign(child):
            print("run_owned: no Job Object for this job; stop falls back to taskkill /PID /T", file=sys.stderr)
            job = None
    else:
        child = subprocess.Popen(wrapper, stdin=subprocess.DEVNULL, start_new_session=True)
    _write_json(Path(ready_file), {"pid": child.pid})
    while child.poll() is None:
        if signalled or stop_file.exists():
            grace = _requested_grace(stop_file)
            outcome = _terminate(child, job, grace)
            _write_json(stop_file, {"grace": grace, "outcome": outcome})
            return 0
        time.sleep(POLL_SECONDS)
    os.close(lock_fd)
    return 0


def _wrap(argv: list[str]) -> int:
    """The job's leader: wait until `start` has written the record, run the command, record its exit
    status atomically, exit with it."""
    rc_file, name, token, go_file, stop_file, cmd = Path(argv[0]), argv[1], argv[2], Path(argv[3]), argv[4], argv[5:]
    deadline = time.monotonic() + GO_SECONDS
    while not go_file.exists():
        if time.monotonic() >= deadline:
            print(f"run_owned: job {name!r} was never recorded; the command did not run", file=sys.stderr)
            return EXIT_NO_STATUS
        time.sleep(0.02)  # start writes the go file right after the record; a short poll keeps start fast
    env = {**os.environ, "RUN_OWNED_JOB": name, "RUN_OWNED_PGID": str(os.getpid()), "RUN_OWNED_TOKEN": token,
           "RUN_OWNED_STOP_FILE": stop_file}
    exe = (shutil.which(cmd[0]) or cmd[0]) if WINDOWS else cmd[0]
    try:
        child = subprocess.Popen([exe, *cmd[1:]], stdin=subprocess.DEVNULL, env=env)
    except OSError as exc:
        print(f"run_owned: cannot run {cmd[0]!r}: {exc}", file=sys.stderr)
        rc = 127
    else:
        rc = child.wait()
        rc = rc if rc >= 0 else 128 - rc
    # `stop` writes the stop file before any TERM, so its presence here means the command ended because it
    # was stopped. The group TERM can reach the command before this wrapper, which would otherwise record an
    # exit status in that window and report a stopped job as exited.
    if not os.path.exists(stop_file):
        _write_text(rc_file, f"{rc}\n")
    return rc


def _requested_grace(stop_file: Path) -> float:
    try:
        return float(json.loads(stop_file.read_text(encoding="utf-8"))["grace"])
    except (OSError, ValueError, KeyError, TypeError):
        return 10.0


def _terminate(child: subprocess.Popen, job: _Job | None, grace: float) -> str:
    """End the child's job: macOS/Linux TERM its group, KILL after `grace` (the child is ours and
    unreaped, so its pgid is too); Windows waits `grace` for it to exit, then ends the Job Object."""
    if WINDOWS:
        def alive() -> bool:
            return job.active() > 0 if job else child.poll() is None
    else:
        _signal_group(child.pid, TERM)

        def alive() -> bool:
            return _group_alive(child.pid)
    deadline = time.monotonic() + grace
    while alive() and time.monotonic() < deadline:
        child.poll()
        time.sleep(POLL_SECONDS / 2)
    outcome = "stopped"
    if alive():
        if job:
            job.terminate()
        else:
            _signal_group(child.pid, KILL)
        outcome = "killed"
    child.wait()
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
        rc = exit_status(rec)
        return EXIT_NO_STATUS if rc is None else rc
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        rc = exit_status(rec)
        if rc is not None:
            return rc
        # Without a supervisor, only a leader whose start time matches keeps the wait going.
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
    own = os.getpid() if WINDOWS else os.getpgrp()
    if rec.pgid <= 1 or rec.pgid == own:
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: not a group run_owned created")
    if rec.stopped is not None or exit_status(rec) is not None:
        return _Target(rec, proc, "ended")
    if supervised(sdir, name):
        return _Target(rec, proc, "supervised")
    leader = _leader_is_ours(rec)
    if leader is None:
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: its supervisor is gone and it cannot confirm pid "
                         f"{rec.pid} is the job it recorded (no start time, `ps` unavailable, or a reused pid)")
    if leader and not _leads_its_group(rec):
        raise OwnedError(f"refusing to signal pgid {rec.pgid}: the verified leader, pid {rec.pid}, does not lead "
                         "that group (the record was edited, or the leader moved groups)")
    return _Target(rec, proc, "signal" if leader else "ended")


def _leads_its_group(rec: Record) -> bool:
    """The fallback signals rec.pgid once pid and start time match, so the group must be the verified
    leader's own: run_owned makes the leader its group's leader (Windows: pgid is the leader's pid)."""
    if rec.pgid != rec.pid:
        return False
    if WINDOWS:
        return True
    try:
        return os.getpgid(rec.pid) == rec.pgid
    except OSError:
        return False


def stop_many(sdir: Path, jobs: list[tuple[str, subprocess.Popen | None]], grace: float = 10.0) -> list[str]:
    """Stop every named job at once: a supervised job through its stop file; a job whose supervisor is
    gone through its stop file and TERM to its verified group, KILL after `grace` (Windows: taskkill
    /PID <leader> /T). Each name is checked before anything is signalled or written. Pass a job's Popen
    (its supervisor) when this process started it, so it is reaped. Returns one outcome line per job;
    raises OwnedError if a supervisor did not confirm."""
    targets = [_target(sdir, name, proc) for name, proc in jobs]
    for t in targets:
        if t.how in ("supervised", "signal"):
            _write_json(_stop_path(sdir, t.rec.name), {"grace": grace})
        if t.how == "signal":
            _signal_group(t.rec.pgid, TERM)

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
                    _signal_group(t.rec.pgid, KILL)
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
        stop_file = _stop_path(sdir, t.rec.name)
        if t.how == "supervised":
            reply = json.loads(stop_file.read_text(encoding="utf-8")) if stop_file.is_file() else {}
            outcome = reply.get("outcome")
        else:
            outcome = "killed" if t.rec.name in killed else "stopped"
        stop_file.unlink(missing_ok=True)
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
    if argv[:1] == [WRAP]:
        return _wrap(argv[1:])
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
