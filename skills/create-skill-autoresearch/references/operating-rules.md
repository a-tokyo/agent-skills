# Operating Rules

The rules every session of a build follows: the main session and every phase worker.
A worker reads this file first.

**Paths.** Run every command from the workspace root (created in 1.6); every path in the factory is relative to it.
`<skill-dir>` is the factory skill's base directory, given in the handoff.

**Bundled scripts** run as `python3 <skill-dir>/scripts/<name>.py` (each has `--help`; Python 3.9+ and stdlib on
macOS, Linux or Windows, where `py -3` replaces `python3` — [hosts.md](hosts.md)).

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

## Phase workers

- Do the phase in your file and nothing else. Do not invoke the create-skill-autoresearch skill or open its
  SKILL.md: your handoff and phase file are the whole instruction set.
- You cannot ask the user. Where your phase asks to present something to the user or wait for an answer (an
  interactive run), write the question to `work/handoffs/state.yaml` `questions:` and return; the main session asks,
  records the answer in `answers:` and dispatches a fresh worker to continue.
- When your context or budget runs low, stop at a clean point (no half-applied experiment), update the
  fields of `work/handoffs/state.yaml` it already holds (`best_score`, `best_commit`, `experiments_run`,
  `remaining_budget`, `validation_score`, `top_concerns`, `last_updated`) and return; the main session dispatches a fresh worker that
  resumes from it.
- Write every output to disk. Your final reply is a summary of at most 15 lines: what you did, the files you wrote,
  the numbers (scores, Δ, exit codes, experiments run), and the next step. Never paste file contents into it.
