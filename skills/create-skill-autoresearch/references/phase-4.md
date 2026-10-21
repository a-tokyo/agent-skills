# Phase 4: Autoresearch

The Phase 4 worker's file, dispatched by the main session: invoke the **autoresearch skill** to iterate the skill
draft against the evaluation rubric, then run the 4.6 description pass. Its inputs: `work/handoffs/state.yaml`,
[operating-rules.md](operating-rules.md), this file, `work/evaluation/rubric.yaml`, `work/evaluation/data-split.yaml`,
`work/research/00-synthesis.md`, and the tails of `autoresearch.jsonl` and `autoresearch.ideas.md` when they exist
(a resume, or an ITERATE that seeded new ideas). Section 4.2 is also the Phase 3 worker's: it records the split
before the 3.5 baseline.

## Contents
- 4.1 Configure Autoresearch (the setup answers and the three records the factory owns)
- 4.2 Data Split (splits, leave-one-out rotation, large sets, overfitting detection)
- 4.3 Let Autoresearch Run (ideas backlog, ending the loop)
- Loop Conventions the Factory Owns
- 4.6 Description Pass

---

## 4.1 Configure Autoresearch

Answer every autoresearch setup question up front from this list, so its setup confirms once and the
loop never waits on a user. Paths are relative to the workspace root, the autoresearch working
directory. Session files (`results.tsv`, `run.log`, `autoresearch.jsonl`, `autoresearch.ideas.md`)
live there during the session and are archived to `work/experiments/` when it ends or on handoff.

- **Goal**: Improve `<skill-name>` quality as measured by `overall_score` (LLM-as-judge against gold standards, or the objective real-world metric for procedural skills — see [phase-3.md](phase-3.md) 3.4)
- **Metric command**: `./work/evaluation/evaluate.sh` (no argument: the training split, 3.4)
- **Metric extraction**: the value of the `METRIC overall_score=<value>` line in `run.log`
  (regex `^METRIC overall_score=([0-9.]+)$`); the other `METRIC` lines are per-dimension context
- **Direction**: `higher_is_better`
- **In-scope files**: `output/<skill-name>/SKILL.md`, `output/<skill-name>/references/*`, `output/<skill-name>/scripts/*`
- **Out-of-scope files**: `input/`, `work/` for experiment edits; the LOG step still writes the factory's records below and the 4.2 rotation
- **Constraints**: the hard constraints in 3.3; if `work/evaluation/evaluate-checks.sh` exists, it
  must exit 0 **before** measuring — a failing check is a discard, not a measurement
- **Budget**: From rubric config `max_iterations` (default 20)
- **Simplicity policy**: the autoresearch default (simpler wins ties)
- **Loop conventions**: apply "Loop Conventions the Factory Owns" below in every experiment; its noise
  threshold overrides upstream's keep-any-improvement rule

The factory owns three records the upstream loop does not keep. Write them yourself in the LOG step:
- **`autoresearch.jsonl`**: one JSON line per experiment — `experiment`, `commit`, `metric`,
  `status`, per-dimension scores, and `hypothesis` / `learned` / `rollback_reason` — plus
  `"type": "validation_check"` lines (4.2). Resume and Phase 5 read this file.
- **`autoresearch.ideas.md`**: the backlog seeded in 4.3. Read it in every THINK step; tick what you try.
- **`results.tsv`**: the upstream table, unchanged.

## 4.2 Data Split

Record the split in `work/evaluation/data-split.yaml`. With >= 10 gold standards, split **70% training**
(every experiment), **20% validation** (checked adaptively, below) and **10% test** (held out until
Phase 5). With 3-9, use **leave-one-out rotation**: each experiment evaluates all but one, and its LOG
step — after the keep/revert decision — advances the held-out case by rewriting the `training` list in
data-split.yaml and committing that file alone
(`git commit -m "rotate held-out case" -- work/evaluation/data-split.yaml`), so a later revert of
`HEAD~1` undoes an experiment, never the rotation. There is no test split; the 3.6 gate uses every gold standard.

**Large sets (100+ gold standards)**: evaluate a rotating sample of 10-15 training cases per experiment;
run the full training set only on kept experiments and at phase boundaries.

```yaml
# work/evaluation/data-split.yaml
strategy: "70-20-10"  # or "leave-one-out"
training:
  - "input/input-01.md"
  - "input/input-02.md"
  # ...
validation:
  - "input/input-08.md"
  - "input/input-09.md"
test:
  - "input/input-10.md"
# The two keys below are written by `gate.py record` (phase-3.md 3.6) from disk, never by hand.
plugin_eval_available: 0  # exit code of `claude plugin eval --help`; the path follows phase-3.md 3.6
plugin_eval:              # every case 5.0 runs: eval dir names (manual protocol: the gate gold standards)
  - "holdout-03"
  - "trigger-1"
  - "near-miss"
```

For leave-one-out (< 10 gold standards):

```yaml
strategy: "leave-one-out"
all_cases:
  - "input/input-01.md"
  - "input/input-02.md"
  - "input/input-03.md"
rotation: "each experiment holds out a different case"
training:            # this experiment's rotation: every case but the held-out one
  - "input/input-02.md"
  - "input/input-03.md"
```

`evaluate.sh` with no argument scores the current `training` list.

**Overfitting detection** (70-20-10 split): run `evaluate.sh` against the validation set adaptively — not on
a fixed schedule — after every **kept** experiment (every improvement changes the overfitting risk profile),
after a **plateau** (is the ceiling real or training-specific?), and when the training score jumps by more
than 0.05 in one experiment (sudden gains are suspicious). Compare the trends:
- Training up, validation up → healthy improvement, continue
- Training up, validation flat → early warning, monitor closely
- Training up, validation down by more than 0.05 → warn the user that recent changes may be over-fitted and
  offer to generalize them, revert to the last validation-stable commit, or widen rubric criteria that have
  become too narrow

Log validation checks in `autoresearch.jsonl`:
```json
{ "type": "validation_check", "training_score": 0.82, "validation_score": 0.78, "trigger": "kept", "experiment": 10, "timestamp": "..." }
```

**Overfitting detection for leave-one-out** (< 10 gold standards): with no fixed validation set, track
per-case variance — record per-case scores after each kept experiment; if the variance across cases widens while
the mean improves, the skill is specializing for some cases at others' expense. Flag when any single case drops
> 1.0 point while others improve. Log them in `autoresearch.jsonl`: `"per_case_scores": {"input-01": 8.2, "input-02": 7.1, ...}`.

## 4.3 Let Autoresearch Run

The autoresearch skill runs the THINK-EDIT-COMMIT-RUN-MEASURE-DECIDE-LOG cycle (commit-first git
model, revert on no improvement, diversify after plateaus). The factory runs the checks before
MEASURE and writes the jsonl record in LOG, as configured in 4.1.

Seed `autoresearch.ideas.md` before the first experiment with:
- Ideas from research synthesis
- Per-dimension improvement strategies from the rubric
- Patterns observed in gold standards that aren't yet reflected in the skill
- Craft passes from **writing-for-agents** (fallback: [standalone-fallback.md](standalone-fallback.md)): leading-word hunt, no-op/duplication/sediment prune, disclosure rebalance
- Moving deterministic work from prose into `scripts/` where the executor keeps getting it wrong

```markdown
# Experiment Ideas

## From Research Synthesis
- [ ] <idea from research that isn't in the draft yet>
- [ ] <pattern from gold standards not reflected in skill>

## Per-Dimension Improvement
- [ ] <specific idea to improve the weakest dimension>
- [ ] <specific idea to improve the second weakest>

## From Gold Standard Analysis
- [ ] <pattern in reference outputs the skill doesn't produce>

## Craft Passes (writing-for-agents; fallback: standalone-fallback.md)
- [ ] Leading-word hunt: collapse restated qualities and fuzzy gates into pretrained tokens
- [ ] Pruning pass: sentence-level no-op test, duplication and sediment sweep
- [ ] Disclosure rebalance: inline what every branch needs, disclose what only some reach
```

**Ending the loop is the factory's call, not the loop's.** Upstream treats a plateau as advisory, so an
unreachable `target_score` burns the whole budget and reports "exit criteria not met". Override that:

- **A plateau is terminal.** `plateau_window` (default 2) consecutive experiments within the noise threshold (judge
  variance, ~0.2-0.3 on a 1-10 scale, below which gains are not measurable) end the loop: carry best-so-far into Phase 5.
- **Below target is a verdict, not a failure** -- Phase 5.4 grades it (SHIP WITH CAVEATS within
  `caveat_margin` of `target_score`). Honour `baseline_lock` in `state.yaml` the same way, logging the real target
  in the ideas backlog.
- Never ask for repeated re-runs toward an unreachable number: report the ceiling, name the binding
  constraint (target too high, or gains below judge variance), and proceed.

## Loop Conventions the Factory Owns

Upstream autoresearch keeps `results.tsv` and `run.log` and reverts with `git reset`. The factory
adds the rest, and applies it in every experiment:

- **Scoped commits** and **owned processes**: the Git and Background jobs rules in
  [operating-rules.md](operating-rules.md).
- **Repository guard.** Before any `git reset --hard` or `git clean`, assert that
  `git rev-parse --show-toplevel` equals the workspace root, and refuse to revert otherwise. A
  workspace nested in a parent project (a gitignored `builds/`, say) would reset the parent's last
  commit and wipe its uncommitted work; the workspace is its own repository (1.6).
- **Clean reverts.** Discard with `git reset --hard HEAD~1 && git clean -fd -- output/<skill-name>`,
  so a reverted experiment leaves no new untracked files behind. The baseline commit (3.5) is
  experiment 0, so `HEAD~1` always exists.
- **Noise threshold.** Keep a change only if `overall_score` beats the best by more than the judge
  noise threshold: the larger of 0.01 absolute minimum and the per-judge variance measured on the
  baseline (re-run the baseline twice to estimate it). Gains inside the noise are discards.
- **Plateau window.** `rubric.yaml` `plateau_window` (default 2) consecutive experiments within the noise
  threshold is a plateau, and 4.3 ends the loop there.
- **Baseline failure.** If the metric command fails on experiment 0 (no `METRIC overall_score` line,
  non-zero exit), stop and fix `evaluate.sh` — never start the loop against a broken metric.
- **Experiment record.** Append one line to `autoresearch.jsonl` per experiment:

  ```json
  { "experiment": 7, "commit": "abc1234", "metric": 0.81, "status": "keep",
    "scores": { "clarity": 8.1 }, "hypothesis": "...", "learned": "...", "rollback_reason": null }
  ```

- **Context budget.** Read only the tail of `run.log` and the last few jsonl lines per THINK step;
  when the session's context runs low, hand off ([operating-rules.md](operating-rules.md): update state.yaml and return)
  rather than compress history.

## 4.6 Description Pass

Model-invoked skills only, after the loop ends: run **skill-creator**'s description optimizer
(`scripts/run_loop.py`) on the best commit, with the 3.6 trigger and near-miss prompts as its seed queries. Keep the
new description only if the 3.6 trigger cases still pass and `overall_score` does not drop; log the decision in
`autoresearch.jsonl`. Read any absolute score skill-creator reports as a ranking, not a level — its grader passes
vague output — and its with/without Δ as the signal. Without skill-creator or `claude -p` (its trigger test needs
Claude Code), revise by hand against the same queries and log that no trigger rate was measured.

skill-creator's `--eval-set` is a JSON list `[{"query": "...", "should_trigger": true}]` of about 20 queries: the
3.6 trigger and near-miss prompts plus realistic near misses. Pass `--holdout 0` when the set is too small to split,
and `--report <path>` under `work/` in headless runs; the rest: `python3 -m scripts.run_loop --help` from the
skill-creator dir.
