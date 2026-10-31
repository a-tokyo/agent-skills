# Phase 4: Autoresearch

Inputs: `rubric.yaml`, `data-split.yaml`, the synthesis, and the tails of `autoresearch.jsonl` and
`autoresearch.ideas.md` when they exist.

## 4.1 Configure Autoresearch

Answer every autoresearch setup question up front, so the loop never waits. Its session files (`results.tsv`, `run.log`, `autoresearch.jsonl`, `autoresearch.ideas.md`) move to `work/experiments/`
when it ends or on handoff.
- **Goal**: raise `<skill-name>`'s `overall_score` (3.4). **Direction**: `higher_is_better`.
- **Metric command**: `./work/evaluation/evaluate.sh` (the training split); **extraction**: `^METRIC overall_score=([0-9.]+)$` in `run.log`.
- **In scope**: `output/<skill-name>/SKILL.md`, `references/*`, `scripts/*`. **Out of scope**: `input/`, `work/` (LOG still writes the records below and the 4.2 rotation).
- **Constraints**: the 3.3 checklist; `work/evaluation/evaluate-checks.sh`, when it exists, must exit 0 before MEASURE (a failure is a discard).
- **Budget**: `max_iterations`. **Simplicity**: upstream's default. **Conventions**: 4.3, in every experiment; its noise threshold overrides keep-any-improvement.

LOG also writes one `autoresearch.jsonl` line per experiment (resume, the loop guard and Phase 5 read it) and ticks
what it tried in `autoresearch.ideas.md` (read it every THINK); `results.tsv` stays upstream's.

```json
{"experiment": 7, "commit": "abc1234", "metric": 0.81, "held_out": 0.78, "status": "keep", "scores": {"clarity": 8.1}, "hypothesis": "...", "learned": "...", "rollback_reason": null}
```

## 4.2 Data Split

`work/evaluation/data-split.yaml`: `strategy` and the lists (`gate.py record` writes `plugin_eval*`, never by hand):
- **>= 10 gold standards**: `70-20-10`: `training` (every experiment), `validation` (each experiment's held-out
  score), `test` (held out until the 5.0 gate).
- **3-9**: `leave-one-out`: `all_cases`, and `training` holding every case but one. After the keep/revert decision,
  LOG rotates the held-out case by rewriting `training` and committing that file alone (`git commit -m "rotate
  held-out case" -- work/evaluation/data-split.yaml`), so a revert of `HEAD~1` never undoes a rotation. No test
  split: the gate uses every gold standard.
- **Fewer than 3**: before the 3.5 baseline, write >= 3 **synthetic cases** in `work/evaluation/synthetic/`:
  realistic inputs from the study materials with the reference outputs their rules give, varied beyond the gold. `strategy: "synthetic-holdout"`, listed under `validation` (the gold standards are
  `training`); never edited during the loop.
- **100+**: score a rotating sample of 10-15 training cases per experiment, the full set only on kept experiments
  and at phase boundaries.

**The held-out score.** Every experiment line (experiment 0, each experiment kept or not, each 4.6 re-score)
carries `held_out`: that commit's `overall_score` on cases it was not tuned on, measured in MEASURE before the
keep/revert decision with `evaluate.sh <case>` (the mean of several): every `validation` case, or under
leave-one-out (its overfitting check) the `all_cases` entry missing from `training`. Every stop and the ship rule
read it, never the training metric alone. A `metric` above `held_out` by more than the noise threshold is the
**overfitting signal**: no fast path, the loop stays open; generalize the latest changes or revert to the last
commit without the gap. No numeric `held_out` counts
as not measured: no fast path, the loop stays open, and metric-first the skill is `UNSHIPPED`.

## 4.3 Let Autoresearch Run

Autoresearch runs THINK-EDIT-COMMIT-RUN-MEASURE-DECIDE-LOG. Before experiment 1, seed `autoresearch.ideas.md` with
`- [ ]` items: ideas from the synthesis, gold patterns the skill misses, a strategy per weak dimension, the craft
passes (writing-for-agents: leading-word hunt; no-op, duplication and sediment prune; disclosure rebalance), and
deterministic work the executor gets wrong moved into `scripts/`. In every experiment:
- **Scoped commits and owned processes** (operating-rules.md). **Repository guard**: before `git reset --hard` or
  `git clean`, assert `git rev-parse --show-toplevel` is the workspace root; otherwise refuse. **Clean reverts**: `git reset --hard HEAD~1 && git clean -fd -- output/<skill-name>`.
- **Noise threshold**: keep a change only if `overall_score` beats the best by more than the noise threshold (the
  latest `noise_estimate`, never below 0.01 absolute minimum); gains inside it, judge variance, are discards.
- **Baseline failure**: if the metric command fails on experiment 0 (no `METRIC overall_score` line, non-zero exit),
  stop and fix `evaluate.sh`; never loop on a broken metric.
- **Context budget**: per THINK step read only the tail of `run.log` and the last few jsonl lines; when context runs
  low, hand off (4.4) rather than compress history.

**Ending the loop is the factory's call**, every stop reading `held_out`:
- **Overfitting is not done**: while the last kept `metric` exceeds its `held_out` by more than the noise threshold,
  or a line lacks a numeric `held_out`, no stop applies until `max_iterations` experiments after experiment 0.
- **Plateau**: `plateau_window` consecutive experiments whose `held_out` stays within the noise threshold of the best
  kept end the loop; carry best-so-far into Phase 5.
- **Ceiling and target**: a best kept `held_out` of 1.0 ends it; at or above `target_score`, the first experiment
  not beating the best by more than the noise threshold ends it.
- **Below target is a verdict** (5.4), as is `baseline_lock` (log the real target in the ideas). Never ask for
  re-runs toward an unreachable number: report the ceiling, name the binding constraint, proceed.
- **With `metric: answer-key` this metric is the ship decision** (SKILL.md 5.4): a skill edited after its last scored
  experiment ships on no metric, so log every re-score with its commit and `held_out`.
- **The loop guard enforces this**: `python3 <skill-dir>/scripts/check_gate.py --loop` prints `LOOP DONE: <reason>`
  or `LOOP OPEN: <reason>`, and while done the kill guard denies the metric command. On the panel-verdict path a
  newer panel ITERATE or BLOCK reopens it for one experiment; a description-only change (4.6) may be scored.

## 4.6 Description Pass

Model-invoked skills only, after the loop: run **skill-creator**'s description optimizer (`python3 -m
scripts.run_loop`, `--help` in its dir) on the best commit with `--eval-set` (about 20 `{"query", "should_trigger"}`
items: the 3.6 trigger and near-miss prompts plus realistic near misses), `--holdout 0` on a small set, `--report`
under `work/` headless. Keep the new description only if
the 3.6 trigger cases still pass and `overall_score` does not drop; log the decision, a kept one as an experiment
line with commit, score and `held_out`. Read its absolute scores as a ranking, its with/without Δ as the signal.
Without skill-creator or `claude -p`, revise by hand against the same queries and log that no trigger rate was measured.
