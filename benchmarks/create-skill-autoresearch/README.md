# Benchmark — create-skill-autoresearch

End-to-end factory benchmark: the **same model builds the same skill from the same brief** with no
skills, with Anthropic's single-pass `skill-creator`, or with the full `create-skill-autoresearch`
pipeline. The **produced skill** is then scored by executing it against held-out answer keys, not by
reading it.

## Tasks

**task** — `conventional-commits` ([`task/brief.md`](task/brief.md)). Turn a staged diff summary
into a commit message that follows a team's conventions beyond the public spec (scope = top-level
directory, `build(deps)`, dual breaking-change markers, `- ` bullet bodies, `revert:` + SHA body).
Six gold standards; three holdouts recombine the conventions.

**task-2** — `purchase-order-builder` ([`task-2/brief.md`](task-2/brief.md)). Turn a free-text
purchase request into a purchase-order JSON by looking items, vendors, cost centers, sites and
exchange rates up in replaceable master-data CSVs. A hybrid skill: deterministic lookups belong in
bundled scripts, judgement in the prose. The rules are only partly written down, and the written
policy is deliberately stale (wrong approval thresholds, "cheapest wins"); seven gold standards show
actual practice — preferred-before-cheapest after FX and vendor contract discounts, blocked vendors,
minimum order quantities, number-word quantities, merged duplicate lines, a software-spend approval
floor, exact exception codes and details. Five holdouts use **different master data** (other SKUs,
prices, vendors, codes, a new currency), so a skill that memorises gold outputs fails. Gold
standards and the answer key come from one seeded generator and reference solver
([`task-2/tools/`](task-2/tools/)), which no arm ever sees.

Holdouts never reach a builder in either task.

## Arms

| Arm | What the builder gets |
|-----|----------------------|
| `bare` | brief + gold standards + materials, no skills |
| `skill-creator` | the same + Anthropic's `skill-creator` |
| `factory-v021` | the same + `create-skill-autoresearch` v0.2.1 (agent-skills commit `99652ee`) with its July companions (autoresearch, premortem, handoff) |
| `factory` | the same + the current `create-skill-autoresearch` with its declared companions ([`companions/`](companions/)) |

`tribunal` is absent from every arm, so Phase 5 runs the factory's inline panel. Factory arms run
unattended with a 6-iteration autoresearch budget.

A factory that ships `hooks/hooks.json` (v0.3.0 from cycle 3 on) loads as a plugin
(`claude -p --plugin-dir <run>/plugins/create-skill-autoresearch`), so its kill guard — a `PreToolUse`
hook that blocks `pkill`, `killall` and pgrep-fed kills — is active; the skill is then listed as
`create-skill-autoresearch:create-skill-autoresearch`. Earlier `factory` runs and `factory-v021` place
the skill under the fake HOME's `~/.claude/skills/` instead. [`arms/smoke-kill-guard.sh`](arms/smoke-kill-guard.sh)
is a one-call check that the guard blocks a dummy `pkill` under that load. Its single run on
2026-10-18 (Haiku 5.5, Claude Code 2.1.301) passed: the agent got the guard's message as a tool error,
the session continued, and `fidelity.py` counted 0 kill commands and 1 blocked attempt.

## Scoring — deterministic, arm-independent, fixed before any run

- **task** — [`scoring/score.mjs`](scoring/score.mjs): a fixed executor (Haiku 5.5, thinking off)
  gets the produced SKILL.md as its system prompt and each holdout as the user message; the commit
  message is parsed field by field against the answer key. execution 0.60 · compliance 0.25 · craft 0.15.
- **task-2** — [`scoring/score-task2.mjs`](scoring/score-task2.mjs): a fixed **agentic** executor
  (Haiku 5.5 via `claude -p`, tools on, isolated HOME, env allowlist, 15-minute timeout) gets the
  produced skill installed and a workspace holding one holdout request plus the holdout master data,
  and must write `po.json` — checked field by field (SKUs, vendors, quantities, EUR prices and totals
  within 0.01, approval tier, review flag, exception codes and details). Two executor runs per
  holdout. execution 0.70 · compliance 0.20 (frontmatter, model-invoked, < 300 lines, scripts present
  and referenced, no gold literals hard-coded in scripts) · craft 0.10 (description what + when,
  third person, ≥ 3 script functions, a concrete example). An executor run that times out or exits
  non-zero without writing `po.json` is retried once. A second run that exits non-zero without
  `po.json`, or times out with an infrastructure error in its transcript (the API/network patterns
  `arms/run-arm.sh` uses), is an env failure for that case — excluded from the execution mean and
  reported in `score-report.json`, never scored 0. A second timeout without one is the skill's result
  and scores 0. The scorer gained the env-failure rule after the `j-*` run; there, the only executor
  runs without `po.json` (two, in one Haiku build) ended on the executor's normal final reply,
  stopping at the skill's own parser defect, so it changes no `j-*` number. The published `k-*` run
  was scored under it and had no executor faults, so the later timeout rule changes no `k-*` number.

No LLM judge anywhere. `node scoring/selftest.mjs` and `node scoring/selftest-task2.mjs` must print
`SELFTEST PASS` before any score is trusted. A live run of `node scoring/score-task2.mjs
scoring/fixtures/t2-good-skill` (the task-2 reference solver wrapped as a skill) scored 1.000, and of
`scoring/fixtures/t2-bad-skill` (a generic skill) 0.22, on 2026-10-15.

**Why this scorer, and what the factory uses where:** [EVALUATORS.md](EVALUATORS.md) compares it with
`claude plugin eval` (regex and LLM graders) and skill-creator's eval on six task-2 skills. It was the
most stable and cheapest, but measures absolute quality only; plugin eval with deterministic graders is
the factory's 5.0 uplift gate, and skill-creator's lenient grader is read as a ranking.

## Results — cycle 3 final run (`p-*`, October 2026)

The published v0.3.0 result. Builders Claude Sonnet 5.5 and Haiku 5.5, n=5 per cell, medians with
min–max, `overall` column of [`results/scores.csv`](results/scores.csv). v0.3.0 is the `p-*` run:
the factory at harness `0c94f94` (cycle 3: `gate.py record`, the kill-guard hook, the evaluator
guidance, `check_gate.py --finish`), loaded with `--plugin-dir` so the kill guard was active in all 20
builds. All 20 exited 0 and produced a skill: no environment failures, no retries, no executor
faults. bare, skill-creator and v0.2.1 are the `m-*` baselines, as in the cycle-2 run below.

| Task | Model | bare | skill-creator | factory v0.2.1 | **factory v0.3.0** |
|------|-------|------|---------------|----------------|--------------------|
| task-2 | Sonnet 5.5 | 0.975 (0.932–0.980) | 0.964 (0.908–0.975) | 0.975 (0.933–0.980) | **0.957 (0.932–1.000)** |
| task-2 | Haiku 5.5 | 0.969 (0.937–0.980) | 0.950 (0.943–0.980) | 0.969 (0.958–0.989) | **0.958 (0.908–0.989)** |
| task | Sonnet 5.5 | 0.971 (0.886–0.971) | 0.921 (0.921–0.971) | 0.946 (0.836–0.971) | **0.946 (0.821–0.971)** |
| task | Haiku 5.5 | 0.896 (0.846–0.921) | 0.896 (0.846–0.921) | 0.871 (0.836–0.943) | **0.886 (0.818–0.921)** |

Median build cost, wall time · input tokens · output tokens (counted as in the cycle-2 table below;
v0.2.1 excludes its killed attempts). Reported, not gated.

| Task | Model | factory v0.2.1 | factory v0.3.0 (`p-*`) | v0.3.0 ÷ v0.2.1 (wall · in · out) |
|------|-------|----------------|------------------------|-----------------------------------|
| task-2 | Sonnet 5.5 | 8.2 min · 3.12M · 81k | 38.1 min · 9.53M · 162k | 4.66× · 3.06× · 2.01× |
| task-2 | Haiku 5.5 | 12.2 min · 7.92M · 194k | 40.0 min · 11.49M · 183k | 3.28× · 1.45× · 0.94× |
| task | Sonnet 5.5 | 9.3 min · 1.78M · 46k | 15.7 min · 2.24M · 63k | 1.68× · 1.25× · 1.38× |
| task | Haiku 5.5 | 17.6 min · 4.08M · 96k | 17.5 min · 5.49M · 134k | 0.99× · 1.35× · 1.40× |

### Release criterion (criterion 4, Amendment 2)

| Task | Model | v0.2.1 floor | bare floor | v0.3.0 median | vs v0.2.1 | Completed (v0.3.0 · v0.2.1) | Result |
|------|-------|--------------|------------|---------------|-----------|-----------------------------|--------|
| task-2 | Sonnet 5.5 | 0.975 − 0.068 = 0.907 | 0.975 − 0.068 = 0.907 | 0.957 | −0.018 | 5/5 · 5/5 | PASS |
| task-2 | Haiku 5.5 | 0.969 − 0.081 = 0.888 | 0.969 − 0.081 = 0.888 | 0.958 | −0.011 | 5/5 · 5/5 | PASS |
| task | Sonnet 5.5 | 0.946 − 0.150 = 0.796 | 0.971 − 0.150 = 0.821 | 0.946 | ±0.000 | 5/5 · 5/5 | PASS |
| task | Haiku 5.5 | 0.871 − 0.125 = 0.746 | 0.896 − 0.103 = 0.793 | 0.886 | +0.015 | 5/5 · 5/5 | PASS |

Every cell meets the quality condition. **The gain condition is met only by task with Haiku: median
+0.015, well inside its 0.125 pooled spread** — a gain the criterion counts, not a measured
improvement. Completion ties at 5/5 everywhere, and completion does not require a verdict (three builds
ended without one; see below). v0.3.0 is below bare in all four cells (−0.018, −0.011, −0.025, −0.010),
each inside the pooled spread.

### Fidelity (criteria 7, 8, 9, 12, 13)

From `python3 scoring/fidelity.py "$BENCH_RUNS_ROOT"/p-*` with the validator published beside this README,
and `check_gate.py --finish` on each workspace.

| Measure | Bar | `k-*` (cycle 2) | `p-*` (cycle 3) |
|---------|-----|-----------------|-----------------|
| Gate artifact at its canonical path | — | 20 of 20 | 19 of 20 |
| Validator passes (criterion 7) | ≥ 18 of 20 | 19 of 20 | **19 of 20** |
| "artifact cases differ" failures (criterion 12) | 0 | 0 | **0** |
| Stale gates · shipped verdicts on an invalid gate | — | 0 · 0 | 0 · 0 |
| Pattern-kill commands executed (criteria 8, 13) | 0 | 0 | **0** |
| Pattern-kill attempts blocked by the guard | — | n/a (no guard) | 0 |
| Builds killed from outside | 0 | 0 | 0 |
| Builds that ended without a verdict (no `BENCHMARK.md`) | — | 3 | **3** |
| Below-target verdicts (`UNSHIPPED`) | — | 7 | 4 |
| Below-target verdicts labelled shipped (criterion 9) | 0 | 0 | **0** |
| Builds that ran `check_gate.py --finish` | — | n/a | 17 of 20 |

- **Criterion 7 is met: 19 of 20.** The failure, task-2 Haiku 3, never ran the gate. It skipped 3.6
  and 5.0 (no `gate.py record`, no `gate.py`), so `check_gate.py` reports "plugin_eval is not
  recorded". It then ran `check_gate.py` and `--finish` and wrote `Verdict: UNSHIPPED`. The label is
  honest, but a skipped gate is a rule break the validator reports and cannot prevent: `--finish`
  accepts UNSHIPPED without a valid gate, by design.
- **Criterion 12 is met:** no build failed "artifact cases differ" (cycle 2's final run had two).
- **Criteria 8 and 13: met, with the guard untested.** No build executed a pattern kill. The guard
  was loaded in all 20 builds and blocked nothing, because no builder tried one, so this run does not
  show the guard stopping a kill. The one-call smoke test (Arms, above) is the evidence that it does.
- **Criterion 9 is met.** All four below-target builds (task Haiku 2 and 3, task-2 Haiku 3, task-2
  Sonnet 5) wrote `UNSHIPPED`, and none labels its output shipped.
- **Honest negative: three Haiku builds again ended without a verdict.** Task Haiku 4 and 5 and
  task-2 Haiku 5 wrote no `BENCHMARK.md`. Each had a valid gate, ran the premortem (5.1), wrote its risk
  list as its reply and ended the turn during 5.1/5.2, so `claude -p` exited 0 before the panel's
  verdict. None of the three ran `check_gate.py --finish`. `--finish` detects this state (each reports
  "no BENCHMARK.md at the build root") but cannot enforce it: a build that ends its turn never runs it.
  This is the same count and the same phase as `k-*`; cycle 3 did not fix it.

### Honest reading

- **No regression beyond noise, and no improvement.** v0.3.0 ties v0.2.1 on task Sonnet, is +0.015
  on task Haiku and −0.018 / −0.011 on the two task-2 cells, all inside the pooled spreads. Against
  `k-*` (cycle 2) the medians moved −0.007, −0.011, −0.025 and +0.015 in the same order, also inside
  run-to-run noise.
- **v0.3.0 beats bare in no cell**, as in cycle 2: these tasks are near the models' ceiling (see the
  cycle-2 reading below).
- **v0.3.0 costs much more on task-2.** Wall time is 4.66× v0.2.1 on task-2 Sonnet and 3.28× on task-2
  Haiku, and input tokens are higher in every cell (1.25×–3.06×). The task-2 gate runs `claude plugin
  eval` with 10 cases × 3 runs × 2 arms; this run did not time the gate separately.
- **Weakest builds:** task Sonnet 1 (0.821, execution 0.702) and task Haiku 4 (0.818, craft 0.333).

Reproduce from the workspace's benchmarks directory (the matrix driver lives there, not in this repo):
`FACTORY_V030_DIR=<factory> caffeinate -dimsu ./drive-matrix.sh cells-v030-c3.txt 6`, where
`<factory>` is `skills/create-skill-autoresearch` at agent-skills `11b8269` (byte-identical to harness
`0c94f94`). A single build is `arms/run-and-score.sh factory <model> <run-id> [task-2]` (see Reproduce).

## Cycle-2 run (`k-*`, October 2026), superseded by `p-*`

Builders Claude Sonnet 5.5 and Haiku 5.5, n=5 per cell, medians with min–max. v0.3.0 is the `k-*`
run (factory at harness commit `a3fa032`: the cycle-2 gate runner, validator and process wrapper,
plus the owned gate lifecycle, the hash-bound gate record and the validator-before-ship rule). All 20
builds exited 0 and produced a skill, with no environment failures, no retries and no executor faults.
Baselines are `m-*`. Per-run rows are in [`results/scores.csv`](results/scores.csv) and
[`results/runs.csv`](results/runs.csv). Environment failures (sessions killed from outside,
`env_failure=1`) are retried once and excluded from every number here.

| Task | Model | bare | skill-creator | factory v0.2.1 | **factory v0.3.0** |
|------|-------|------|---------------|----------------|--------------------|
| task-2 | Sonnet 5.5 | 0.975 (0.932–0.980) | 0.964 (0.908–0.975) | 0.975 (0.933–0.980) | **0.964 (0.932–1.000)** |
| task-2 | Haiku 5.5 | 0.969 (0.937–0.980) | 0.950 (0.943–0.980) | 0.969 (0.958–0.989) | **0.969 (0.953–0.980)** |
| task | Sonnet 5.5 | 0.971 (0.886–0.971) | 0.921 (0.921–0.971) | 0.946 (0.836–0.971) | **0.971 (0.971–0.971)** |
| task | Haiku 5.5 | 0.896 (0.846–0.921) | 0.896 (0.846–0.921) | 0.871 (0.836–0.943) | **0.871 (0.786–0.896)** |

Median build cost per arm: wall time · input tokens · output tokens. Input counts uncached, cache-write
and cache-read tokens summed over every transcript of the build, subagents included
([`arms/sum-tokens.mjs`](arms/sum-tokens.mjs)); each column's median is taken on its own.

| Task | Model | bare | skill-creator | factory v0.2.1 | factory v0.3.0 |
|------|-------|------|---------------|----------------|----------------|
| task-2 | Sonnet 5.5 | 2.5 min · 0.24M · 18k | 6.6 min · 0.37M · 22k | 8.2 min · 3.12M · 81k | 28.6 min · 5.64M · 121k |
| task-2 | Haiku 5.5 | 4.2 min · 0.75M · 51k | 4.0 min · 0.81M · 50k | 12.2 min · 7.92M · 194k | 49.5 min · 18.31M · 314k |
| task | Sonnet 5.5 | 0.6 min · 0.12M · 4k | 0.7 min · 0.18M · 4k | 9.3 min · 1.78M · 46k | 11.1 min · 3.04M · 65k |
| task | Haiku 5.5 | 0.7 min · 0.11M · 7k | 2.4 min · 0.42M · 12k | 17.6 min · 4.08M · 96k | 19.9 min · 5.37M · 161k |

### Release criterion

Criterion 4 as amended on 2026-10-16, after the `h-*` results and before the `i-*` run. Per cell,
v0.3.0's median must be at least v0.2.1's median minus the **pooled** spread (max − min over both
arms' runs in that cell), and at least bare's median minus the pooled spread of v0.3.0 and bare.
v0.3.0 must also gain in median overall or completion rate in at least one cell. Wall time and
tokens are reported above, not gated. Completion is exit 0 with a skill produced, excluding
environment failures (v0.2.1 task Haiku needed four retries after sessions were killed from outside).

| Task | Model | v0.2.1 floor | bare floor | v0.3.0 median | vs v0.2.1 | Completed (v0.3.0 · v0.2.1) | Result |
|------|-------|--------------|------------|---------------|-----------|-----------------------------|--------|
| task-2 | Sonnet 5.5 | 0.975 − 0.068 = 0.907 | 0.975 − 0.068 = 0.907 | 0.964 | −0.011 | 5/5 · 5/5 | PASS |
| task-2 | Haiku 5.5 | 0.969 − 0.036 = 0.933 | 0.969 − 0.043 = 0.926 | 0.969 | ±0.000 | 5/5 · 5/5 | PASS |
| task | Sonnet 5.5 | 0.946 − 0.135 = 0.811 | 0.971 − 0.085 = 0.886 | 0.971 | +0.025 | 5/5 · 5/5 | PASS |
| task | Haiku 5.5 | 0.871 − 0.157 = 0.714 | 0.896 − 0.135 = 0.761 | 0.871 | ±0.000 | 5/5 · 5/5 | PASS |

Every cell meets the quality condition. The gain condition is met on median overall in one cell
(task with Sonnet, +0.025); completion ties at 5/5 everywhere. Completion as defined does not require
a verdict: three v0.3.0 builds stopped before Phase 5 wrote one (see Fidelity), and their skills are
scored like the rest.

### Fidelity of unattended builds (Cycle 2 criteria 7–9)

Counted by [`scoring/fidelity.py`](scoring/fidelity.py) over every build's run directory: the
factory's own validator (`check_gate.py`) on the workspace, the commands the builder issued (Bash
tool calls in the session transcripts under `home/.claude/projects/`, not text it read), and the
verdict statement in `BENCHMARK.md`. Since cycle 3 it counts kill attempts the kill guard blocked
(`kill_blocked`: the call's tool result carries the guard's message) apart from kill commands that ran
(`kill_commands`, the criterion-8 count); no run before cycle 3 loaded the guard. `i-*` (harness `8f6a323`) is the last run before cycle 2, when
the same rules were prose; `j-*` (harness `5ca4fa0`) is the first cycle-2 run; `k-*` is the published
run. The validator changed between `j-*` and `k-*`: the current one also requires a finished
`gate.json` carrying skill and case hashes, which no earlier build has, so it fails every `i-*` and
`j-*` build on that alone. The gate rows therefore show both validators: the `j-*`-era one
(harness `5ca4fa0`) and the current one, which criterion 7 measures `k-*` against. "Current" here is
the validator `k-*` ran with (harness `6d3b6ab`); the one after it is under **After `k-*`** below.

| Measure | Bar | `i-*` | `j-*` | `k-*` (published) |
|---------|-----|-------|-------|-------------------|
| Gate artifact at its canonical path | — | 13 of 20 | 20 of 20 | 20 of 20 |
| Validator passes, `j-*`-era `check_gate.py` | — | 2 of 20 | 18 of 20 | 19 of 20 |
| Validator passes, current `check_gate.py` (criterion 7) | ≥ 18 of 20 | 0 of 20 | 0 of 20 | 19 of 20 |
| Stale gates (skill or suite changed after the gate) | — | n/a | n/a | 0 |
| Shipped verdicts on an invalid gate (`j-*`-era · current validator) | — | 12 · 14 | 2 · 16 | 0 · 0 |
| Builds that issued `pkill` / `killall` / a pgrep-fed kill (criterion 8) | 0 | 1 of 20 | 1 of 20 | 0 of 20 |
| Builds killed from outside (criterion 8) | 0 | 0 | 0 | 0 |
| Sibling builds whose gate run a kill interrupted | — | 1 | 3 | 0 |
| Builds that ended without a verdict (no `BENCHMARK.md`) | — | 0 | 0 | 3 |
| Below-target verdicts | — | 6 | 4 | 7 |
| Below-target verdicts labelled shipped (criterion 9) | 0 | 3 | 0 | 0 |

- **Criterion 7 is met: 19 of 20 builds pass the validator** against a bar of 18. The failure is
  task-2 Haiku 3: its near-miss case names a `fixture.sh` that does not exist, so all six near-miss
  runs errored and `check_gate.py` refuses the artifact ("case 'near-miss' arm 'with' has 0 completed
  runs; 3 required"). Two caveats sit under the 19. First, task-2 Haiku 2 validates with `gold=0`
  (the validator gap below); counting a gate with no gold-standard case as a failure gives 18 of 20,
  still at the bar. Second, task-2 Haiku 2 and 3 never saw their gate's result. Each started the gate
  with `gate.py --detach` 26 s and 35 s before its session ended; Haiku 2 polled once and Haiku 3
  never. The detached gate ran on in its own process group and finished 5.5 and 4.5 minutes after the
  builder had exited. Their wall times (607 s and 419 s) stop at session end and leave the gate out.
  All 17 builds that wrote a verdict quote `check_gate.py`'s `GATE VALID:` line in `BENCHMARK.md`.
- **Criterion 8 is met.** No `k-*` build issued a pattern kill, none was killed from outside, and no
  gate run was interrupted. All 20 started the gate with `gate.py --detach` under `run_owned.py`, and
  none used `nohup`. In `j-*`, 4 builds launched jobs with `nohup` and 7 used `run_owned.py` at all.
- **Criterion 9 is met.** All seven below-target builds wrote verdict `UNSHIPPED`, and none labels
  its output shipped. Task Sonnet 5 stopped on the 4.3 plateau rule with budget left. `j-*` left that
  case as ITERATE (its task-2 Haiku 3); it is now `UNSHIPPED` too. Task-2 Haiku 1 is `UNSHIPPED` on an
  ESCALATE that an unattended run cannot settle. `UNSHIPPED` is a panel verdict, not a score: four of
  the seven `UNSHIPPED` skills score at or above their cell's median (task Sonnet 5 0.971; task-2
  Haiku 1, 4 and 5 0.969, 0.980 and 0.969).
- **Three builds ended without a verdict**: task Haiku 5 and task-2 Haiku 2 and 3 wrote no
  `BENCHMARK.md`. Each invoked the premortem skill (5.1), wrote its risk list as its reply and ended
  the turn, so `claude -p` exited 0 before the panel (5.2) and the ship decision (5.4). `fidelity.py`
  shows their verdict as `-` because there is no `BENCHMARK.md` to read, and that is the correct
  reading. Criterion 9 holds for them only because they state no verdict at all. No `i-*` or `j-*`
  build stopped this way. Two of the three had also left their gate running detached; task Haiku 5
  waited for its gate, so the detached gate is not the whole cause.

**A validator gap, fixed after this run: gold cases that carry a skill-fired grader were not counted as gold.**
`check_gate.py` treats a plugin case as a should-trigger case whenever it has a `tool_used` grader on
`Skill`, and computes the mean Δ and the Δ <= 0 list over the remaining cases only. Task-2 Haiku 2 put
a `skill-fired` grader on each of its seven gold cases next to their output graders. The factory's own
worked gold case does the same (`references/pipeline-phases.md`, `holdout-03`: `graders/approval.md`
and `graders/skill-fired.md`). So the validator reported `gold=0` and `mean gold delta=n/a` for a gate
whose seven gold cases each ran 3 times per arm, with Δ from +0.389 to +0.500 (mean +0.468). The gate
measured real uplift. But the summary that should feed the verdict came out empty, and the validator
still exits 0 with no gold case counted. Task-2 Haiku 3 uses the same layout, behind its invalid gate.
The factory now classifies a case by its whole grader set (harness `cb4d484`, under **After `k-*`**).
Re-scored with that validator, `k-*` still passes 19 of 20: task-2 Haiku 2 reports `gold=9` (two of
its should-trigger cases also grade output) with mean Δ +0.586, and task-2 Sonnet 5 lists its
`trigger-2` case, which also grades output, under Δ <= 0. Since cycle 3 the validator also reports the
Skill indicator of such a gold case (`trigger-2` fired 3/3) instead of dropping it. The table above keeps
the validator the run shipped with.

Reproduce with `python3 scoring/fidelity.py "$BENCH_RUNS_ROOT"/k-*` (and `j-*`, `i-*`). Pass
`--check-gate <harness@REV>/.agents/skills/create-skill-autoresearch/scripts/check_gate.py` with REV
`5ca4fa0` for the `j-*`-era validator and `6d3b6ab` for the one `k-*` ran with; the default, the
validator published beside this README, fails every build up to `k-*` because their `gate.json` has
no scorer, input or artifact hash. It runs offline, and `python3 scoring/selftest-fidelity.py` must print
`SELFTEST PASS`. An `UNSHIPPED` verdict is its own label. For ITERATE and BLOCK the scanner reads only
the verdict statement, so "the shipped file" elsewhere in the report names the output file and does
not count.

### Honest reading

- **The criterion only rules out large regressions.** With n=5 the pooled spreads are 0.036–0.157,
  so the floors sit between 0.714 and 0.933. A PASS means "no regression larger than run-to-run
  noise", not "as good as".
- **v0.3.0 vs v0.2.1: above in one cell, tied in two, below in one.** Above on task with Sonnet
  (0.971 vs 0.946; all five v0.3.0 builds scored 0.971). Tied on task-2 with Haiku (0.969) and task
  with Haiku (0.871). Below on task-2 with Sonnet (0.964 vs 0.975, −0.011, inside the 0.068 pooled
  spread).
- **v0.3.0 beats bare in no cell.** It ties bare on task-2 with Haiku (0.969) and task with Sonnet
  (0.971), and is below it on task-2 with Sonnet (0.964 vs 0.975) and task with Haiku (0.871 vs
  0.896). v0.2.1 ties bare on both task-2 cells and is below it on both task cells. Today's models
  reverse-engineer a near-perfect skill from a handful of gold standards in under five minutes; a
  discriminating task needs a rule surface too large to infer from a few examples.
- **task-2 with Haiku recovered** from `j-*`'s 0.905 to 0.969. Holdout h3's line items, which failed
  in 6 of 10 `j-*` executor runs, failed in none. What remains is the approval tier: h5 in 6 of 10
  runs, h2 and h3 in 4 each. Three builds (Haiku 1, 4 and 5) also lose the compliance point for
  scripts that SKILL.md never references.
- **task with Haiku is v0.3.0's weakest cell**: 0.871 (0.786–0.896), −0.050 against `j-*`. Two
  builds scored craft 0.333 (Haiku 3 and 4) and three scored execution 0.810 (Haiku 2, 4 and 5).
- **v0.3.0 is slower than v0.2.1 in every cell.** Wall time is 3.51× on task-2 Sonnet, 4.07× on
  task-2 Haiku, 1.19× on task Sonnet and 1.13× on task Haiku. Input tokens are 1.81×, 2.31×, 1.70×
  and 1.32× in the same order; output tokens 1.49×, 1.61×, 1.42× and 1.68×. Against `j-*` it is
  faster in three cells (wall 0.99×, 0.54× and 0.69× on task-2 Sonnet, task Sonnet and task Haiku)
  and slower on task-2 Haiku (1.44×: 49.5 against 34.3 minutes). That cell ranges from 7 minutes
  (Haiku 3, which stopped early) to about 80 minutes (Haiku 1 and 4). v0.2.1 has no eval gate and v0.3.0
  makes it mandatory; this run did not measure how much of the extra time the gate takes.
- **`skill-creator` alone is the cheapest non-bare arm**, and it lands at or below bare on every cell.
- **Every arm sometimes misses task-2's software-spend approval floor.** The h5 approval check is
  the most-failed check in each arm: 8 of 20 h5 executor runs for v0.3.0 and for v0.2.1, 10 of 20
  for bare and for skill-creator.

**What changed between `j-*` and `k-*`** (harness `46fcfef` and `6d3b6ab`):
- **Owned gate lifecycle.** `gate.py` runs itself as the owned job `gate` and refuses a second start.
  `--detach` returns at once, so the builder polls with short `run_owned.py wait gate` calls, and
  `--restart` / `--stop` signal only the recorded process group.
- **Hash binding.** `gate.json` records sha256 hashes of `output/<skill>/` and of the case set.
  `check_gate.py` requires a finished, hashed record and an explicit `"partial": false`, and fails
  when the skill or the suite changed after the gate.
- **Validator before ship.** SHIP or SHIP WITH CAVEATS requires `check_gate.py`'s `GATE VALID:` line
  copied into `BENCHMARK.md`, and any terminal ITERATE (budget spent, or the 4.3 plateau rule) is
  `UNSHIPPED`.

The effect on criteria 7–9:
- **Criterion 7:** validator passes rose from 18 to 19 of 20 on the `j-*`-era validator, and from 0
  to 19 on the current one. Shipped verdicts on an invalid gate fell from 2 to 0.
- **Criterion 8:** builds issuing pattern kills fell from 1 to 0, interrupted sibling gates from 3 to
  0, and `nohup` launches from 4 builds to 0.
- **Criterion 9:** below-target verdicts labelled shipped held at 0, and a plateau stop is now
  `UNSHIPPED`.
- **New failure modes:** two builds left a detached gate running past the end of their session,
  three stopped after the premortem without a verdict, and one validated with `gold=0`.

**After `k-*`** (harness `cb4d484`…`716f524`, cycle-2 round-2 fixes, then cycle 3 to `0c94f94`;
measured by the `p-*` run above):
- **Gold by grader.** A case is gold when any of its graders is score-bearing (anything but
  `tool_used` on `Skill`); only a case graded by nothing but `Skill` is a trigger or near-miss
  indicator. Every gold case needs a numeric Δ, and a gate whose data split records gold standards
  but counts no gold case fails (`no_gold`).
- **Gate bound to its scorer.** `gate.json` also hashes `run-case.sh`, the scorer and fixture files
  under `input/` and `work/evaluation/`, and the artifact; any later change fails "gate inputs changed
  since the gate; re-run 5.0" (`stale_inputs`, a stale gate in `fidelity.py`).
- **Owned processes without `ps`.** `run_owned.py start` takes a per-name lock before checking state,
  so concurrent starts run a job once. Each job runs under a supervisor that holds that lock while it
  lives and stops only its own child group when `stop` writes `work/pids/<name>.stop`, so start
  refusal and stop work where `ps` is missing or denied.
- **Preconditions before detaching.** `gate.py --detach` / `--restart` check the suite, the recorded
  cases, each case's `scaffold_script` and the manual adapter in the same call and exit 3 before
  starting anything (task-2 Haiku 3's missing `fixture.sh` now fails there, not an hour later).
- **Phase 5 runs to a verdict.** 5.1's risk list is an input to 5.2, and the build continues in the
  same turn until `BENCHMARK.md` holds a terminal verdict and no gate job is live — the three builds
  that stopped after the premortem.
- **task-2 timeouts** without `po.json` score 0 unless the transcript shows an infrastructure error
  (see Scoring).

**The transcripts behind the fidelity counts are not published**, nor those behind the h5 and h3
failure counts and the holdout-leak search below (`k-*` and `p-*` alike). The `k-*` session transcripts under the builds'
isolated HOMEs alone are 2,473 files (about 204 MB), and they record the maintainer's machine paths
and environment. The scripts that derive these claims are published (`scoring/fidelity.py`, the
per-check results each scorer writes to `score-report.json`, and the holdout search terms below), so
a re-run of the matrix reproduces the method on fresh transcripts.

**Earlier v0.3.0 runs** stay in `scores.csv`, superseded by `p-*` (and before it `k-*`).
- **`m-*-factory-v030-*`** was the first candidate. It regressed task-2 Haiku (0.932) because builds
  trusted the stale written policy over the gold standards.
- **`f-*`** added the "gold standards win conflicts" rule (task-2 Haiku 0.969, task Haiku 0.871).
  Several of its sessions were killed and retried, and its times are not used.
- **`g-*`** added the tribunal round-1 fixes (mandatory gate, git isolation, metric contract). All
  ten task-2 Sonnet sessions, retries included, were killed from outside (exit 143) and never scored.
- **`h-*`** (harness `5f7fd3c`) added the rule that the factory stops only background jobs whose PIDs
  it captured, and ran clean. Its medians were 0.960 (task-2 Sonnet), 0.937 (task-2 Haiku), 0.946
  (task Sonnet) and 0.971 (task Haiku), and 17 of 20 builds wrote a gate artifact.
- **`i-*`** (harness `8f6a323`) added the tribunal round-2 fixes: a baseline commit before Phase 4;
  user-invoked skills and unavailable plugin eval go to a specified manual with/without protocol
  (`uplift.json`); the panel refuses to start without an uplift artifact; leave-one-out gate deltas
  are labelled in-sample. Its medians were 0.975 (task-2 Sonnet), 0.955 (task-2 Haiku), 0.971 (task
  Sonnet) and 0.886 (task Haiku), and it passed the release criterion in every cell.
- **`j-*`** (harness `5ca4fa0`) replaced the gate and process prose with bundled scripts (`gate.py`,
  `check_gate.py`, `run_owned.py`) and made an exhausted budget a terminal `UNSHIPPED` state. Its
  medians were 0.975 (task-2 Sonnet), 0.905 (task-2 Haiku), 0.946 (task Sonnet) and 0.921 (task
  Haiku), and it passed the release criterion in every cell.
  - Gate fidelity: 18 of 20 builds passed the validator of the time. The two failures, task-2 Haiku 2
    and 4, bypassed `gate.py` and `check_gate.py`, never built the 3.6 suite, and still declared SHIP
    WITH CAVEATS.
  - Pattern kill: task-2 Haiku 3 started `gate.py` with `nohup … &`, then issued `pkill -f "gate.py
    --workspace"` and `pkill -f "claude plugin eval"`. That interrupted the gate runs of task-2
    Haiku 1, Haiku 5 and Sonnet 5, which each re-ran their gate, so no score changed.

### Protocol notes (disclosed)

- **Executor change.** July used Haiku 4.5 at temperature 0; Haiku 5.5 rejects temperature and, with
  thinking on, spent the 512-token budget thinking and returned nothing. Thinking is off, and an empty
  truncated reply now throws instead of scoring 0. July and October numbers are not comparable.
- **Environment failures.** A Claude Code self-update (2.1.300 → 2.1.301) killed every live session
  once. Other builds' machine-wide `pkill -f` cleanup (`pkill -f "plugin eval"`, `pkill -f until`)
  killed more. Each killed session was retried once and never scored itself. Every session now runs
  with `DISABLE_AUTOUPDATER=1`. The factory starts and stops background jobs through its bundled
  `scripts/run_owned.py`; one `i-*` build and one `j-*` build still issued pattern kills, and no
  `k-*` build did (see Fidelity above).
- **Every retry** (all rows are in `results/runs.csv`; killed attempts carry `env_failure=1`):
  - v0.2.1 (`m-*`): task Haiku 2, 3, 4 and 5, and task-2 Haiku 2 were killed (exit 143). The killed
    attempts are kept as `-killed` rows and the retries ran under the original run-id.
  - `f-*`: task Sonnet 1, 2 and 3, and task-2 Haiku 2 and 3, retried under `-retry`.
  - `g-*`: task-2 Sonnet 1 to 5, retried under `-retry`; every retry was killed too (exit 143), so the
    cell was never scored.
  - `ar-*` (factory development experiments, not a published arm): `ar-e1-1`, `ar-e1-2` and `ar-e2-2`
    were killed; their `-killed` rows are kept, the retries ran under the original run-id.
  - `h-*`, `i-*`, `j-*`, `k-*` and `p-*`: no retries.
- Factory arms ran unattended; builds sometimes skip steps and say so in their own reports.

## Results — July 2026 (v0.1/v0.2, task only)

Builders `claude-sonnet-5` and `claude-haiku-4-5`, n=2 (n=1 factory-Haiku), executor Haiku 4.5 at
temperature 0. Overall medians: bare 0.896 / 0.928, skill-creator 0.896 / 0.846, factory 0.896 / 0.896
(Sonnet / Haiku). No overall uplift; the task sat at the models' floor. Rows are kept in `scores.csv`.

## Reproduce

The factory's bundled scripts (`gate.py`, `check_gate.py`, `run_owned.py`) and their offline tests run
on macOS and Linux: the harness `checks` workflow runs them on `ubuntu-latest` in its self-test gate
([run 39074944894](https://github.com/a-tokyo/agent-skills-harness/actions/runs/39074944894), harness
`a3fa032`: 43 script tests, GATE PASS).

```bash
cd benchmarks/create-skill-autoresearch
node scoring/selftest.mjs && node scoring/selftest-task2.mjs   # offline: prove the probes discriminate
python3 scoring/selftest-fidelity.py                           # offline: prove the fidelity probes discriminate
export ANTHROPIC_API_KEY=...                                   # or CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)

# one build + its score; task = conventional-commits, task-2 = purchase-order-builder
# (task-2 scores two executor runs per holdout by default, as published; a 5th argument overrides it)
arms/run-and-score.sh bare sonnet bare-t2-1 task-2
arms/run-and-score.sh skill-creator haiku sc-t2-1 task-2
arms/run-and-score.sh factory sonnet factory-t2-1 task-2

# the pinned v0.2.1 arm: check out agent-skills at 99652ee for the skill, and use companions/
# autoresearch, premortem and handoff only
FACTORY_DIR=/path/to/v0.2.1/create-skill-autoresearch COMPANIONS_DIR=/path/to/v0.2.1-companions \
  arms/run-and-score.sh factory-v021 haiku v021-t2-1 task-2

# regenerate task-2 data and its answer key (deterministic)
python3 task-2/tools/generate.py
```

Each run executes in an **isolated HOME** under `$BENCH_RUNS_ROOT` (default:
`$TMPDIR/create-skill-autoresearch-bench`) — fake `$HOME`, own git config, XDG dirs, a scrubbed
`env -i` allowlist and `DISABLE_AUTOUPDATER=1` — so a `--dangerously-skip-permissions` builder can
never touch the maintainer's real config or credentials. That is containment, not an OS sandbox:
builders run on the host filesystem with `--dangerously-skip-permissions`, so a builder could read
outside its run directory. Holdouts and answer keys are never copied into the run directory, and every
builder transcript, session log and build directory of the October runs, `k-*` and `p-*` included, was searched for holdout paths
(`task-2/holdout`, `task/holdout`, `answer-key.json`, `request-h*.md`): none found. (One `p-*` build, task Haiku 5,
named its own gold-derived key `work/evaluation/answer-key.json`; its contents differ from both holdout keys.)
`SCORE_ONLY=1` re-scores an existing run.

Prereqs: Node 18+, Python 3, the `claude` CLI, and a headless credential as above.
