# Operating Rules

The rules every session of a build follows, the first and each resume. Read this file before Phase 1.

**Paths.** Run every command from the workspace root (created in 1.6); every path in the factory is relative to it.
`<skill-dir>` is the factory skill's base directory, given in the handoff.

**Bundled scripts** run as `python3 <skill-dir>/scripts/<name>.py` (each has `--help`; Python 3.9+ and stdlib on
macOS, Linux or WSL; native Windows is not supported — [hosts.md](hosts.md)).

**Background jobs** (eval runs, `until` loops) go through `run_owned.py`: start one with
`run_owned.py start <name> -- <cmd...>`, wait on or stop it with `wait <name>` / `stop <name>`. It signals only the
group it records in `work/pids/`; a name-pattern kill (`pkill`, `killall`, a process listing and a kill in one call)
also hits sibling builds and the user's processes. Stop only what you started, by its recorded name. Each job runs
under a supervisor that holds `work/pids/<name>.lock` while it lives and stops its own child group when `stop` writes
`work/pids/<name>.stop`, so start refusal and stop need no `ps` (sandboxes deny it). Only when that supervisor is
gone does `stop` signal the group, and only if `ps` confirms the recorded start time; otherwise it refuses. It keeps
`work/pids/` out of git.

**Unattended runs** (a brief, no user at the terminal): where a step asks to confirm, adopt the brief's answer
or the stated default without waiting, and record each assumption in `work/experiments/craft-decisions.md`.

**No credentials** in any file under `work/`: it is committed in the workspace's own repository. Credentials live
in the environment.

**Git.** Stage only the paths a step names (`git add output/<skill-name>`). Never `git add -A`: it sweeps `work/`
and session logs into experiment commits.
