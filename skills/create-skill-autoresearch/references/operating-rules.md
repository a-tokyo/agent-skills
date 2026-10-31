# Operating Rules

Every session of a build, the first and each resume, follows these.

**Paths.** Run every command from the workspace root (1.6); paths are relative to it. `<skill-dir>` is the
directory the factory's SKILL.md was loaded from.

**Scripts** run as `python3 <skill-dir>/scripts/<name>.py` (Python 3.9+, stdlib; macOS, Linux or WSL). Their
`--help` is the manual; `panel.py` is a library `check_gate.py` runs.

**Background jobs** (eval runs, `until` loops) go through `run_owned.py start <name> -- <cmd...>`, then
`wait <name>` / `stop <name>`. Stop only what you started, by its recorded name: a name-pattern kill (`pkill`,
`killall`, a process listing and a kill in one call) also hits sibling builds and the user's processes.

**Unattended runs** (a brief, no user at the terminal): where a step asks to confirm, adopt the brief's answer or the
stated default without waiting, and record each assumption in `work/experiments/craft-decisions.md`.

**No credentials** in any file under `work/` (it is committed); they live in the environment.

**Git.** Stage only the paths a step names (`git add output/<skill-name>`). Never `git add -A`: it sweeps `work/`
and session logs into experiment commits.

## 4.4 Monitor and Handoff

On a phase transition, when context or budget runs low, or on request: invoke the **handoff skill** for
`work/handoffs/HANDOFF-<session>.md`, and write `work/handoffs/state.yaml` for the next session:

```yaml
phase: autoresearch       # interview|research|draft|autoresearch|verify
session: <N>              # +1 on each resume
skill_name: <name>
best_score: <value>       # best overall_score, at best_commit
best_commit: <hash>
experiments_run: <count>
remaining_budget: <count>
held_out: <value>         # last held-out score (4.2)
top_concerns: []
blocked_dimensions: []
last_updated: <ISO timestamp>
baseline_lock: false      # true: a human froze target_score at the best; do not re-derive
```

## 4.5 Resume Protocol

When `work/handoffs/state.yaml` exists, read it and the newest `HANDOFF-*.md`, resume at its phase, then bump
`session`: **interview** re-confirm the parameters; **research** synthesize if the dossier is incomplete; **draft**
read phase-3.md, measure the 3.5 baseline if the draft exists; **autoresearch** read phase-4.md, then
`autoresearch.jsonl`, continue with the remaining budget; **verify** re-run the panel if the last verdict was ITERATE
on the panel-verdict path.
