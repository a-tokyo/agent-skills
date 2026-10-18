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
  non-zero without writing `po.json` is an executor fault, not a skill score: it is retried once, and
  a second fault is an env failure for that case — excluded from the execution mean and reported in
  `score-report.json`, never scored 0. The scorer gained this rule after the October run; there, the
  only executor runs without `po.json` (two, in one Haiku build) ended on the executor's normal final
  reply, stopping at the skill's own parser defect, so it changes no published number.

No LLM judge anywhere. `node scoring/selftest.mjs` and `node scoring/selftest-task2.mjs` must print
`SELFTEST PASS` before any score is trusted. A live run of `node scoring/score-task2.mjs
scoring/fixtures/t2-good-skill` (the task-2 reference solver wrapped as a skill) scored 1.000, and of
`scoring/fixtures/t2-bad-skill` (a generic skill) 0.22, on 2026-10-15.

## Results — October 2026 (v0.2.1 vs v0.3.0)

Builders Claude Sonnet 5.5 and Haiku 5.5, n=5 per cell, medians with min–max. v0.3.0 is the `j-*`
run (factory at harness commit `5ca4fa0`, cycle 2: gate runner, validator and process wrapper bundled
as scripts): 20 of 20 builds completed, no environment failures, no retries. Baselines are `m-*`.
Per-run rows in [`results/scores.csv`](results/scores.csv) and [`results/runs.csv`](results/runs.csv).
Environment failures (sessions killed from outside, `env_failure=1`) are retried once and excluded
from every number here.

| Task | Model | bare | skill-creator | factory v0.2.1 | **factory v0.3.0** |
|------|-------|------|---------------|----------------|--------------------|
| task-2 | Sonnet 5.5 | 0.975 (0.932–0.980) | 0.964 (0.908–0.975) | 0.975 (0.933–0.980) | **0.975 (0.975–1.000)** |
| task-2 | Haiku 5.5 | 0.969 (0.937–0.980) | 0.950 (0.943–0.980) | 0.969 (0.958–0.989) | **0.905 (0.871–0.953)** |
| task | Sonnet 5.5 | 0.971 (0.886–0.971) | 0.921 (0.921–0.971) | 0.946 (0.836–0.971) | **0.946 (0.921–0.946)** |
| task | Haiku 5.5 | 0.896 (0.846–0.921) | 0.896 (0.846–0.921) | 0.871 (0.836–0.943) | **0.921 (0.761–0.946)** |

Median build cost per arm: wall time · input tokens · output tokens. Input counts uncached, cache-write
and cache-read tokens summed over every transcript of the build, subagents included
([`arms/sum-tokens.mjs`](arms/sum-tokens.mjs)); each column's median is taken on its own.

| Task | Model | bare | skill-creator | factory v0.2.1 | factory v0.3.0 |
|------|-------|------|---------------|----------------|----------------|
| task-2 | Sonnet 5.5 | 2.5 min · 0.24M · 18k | 6.6 min · 0.37M · 22k | 8.2 min · 3.12M · 81k | 28.9 min · 8.64M · 131k |
| task-2 | Haiku 5.5 | 4.2 min · 0.75M · 51k | 4.0 min · 0.81M · 50k | 12.2 min · 7.92M · 194k | 34.3 min · 11.91M · 203k |
| task | Sonnet 5.5 | 0.6 min · 0.12M · 4k | 0.7 min · 0.18M · 4k | 9.3 min · 1.78M · 46k | 20.6 min · 3.26M · 72k |
| task | Haiku 5.5 | 0.7 min · 0.11M · 7k | 2.4 min · 0.42M · 12k | 17.6 min · 4.08M · 96k | 28.6 min · 5.73M · 161k |

### Release criterion

Criterion 4 as amended on 2026-10-16, after the `h-*` results and before the `i-*` run. Per cell,
v0.3.0's median must be at least v0.2.1's median minus the **pooled** spread (max − min over both
arms' runs in that cell), and at least bare's median minus the pooled spread of v0.3.0 and bare.
v0.3.0 must also gain in median overall or completion rate in at least one cell. Wall time and
tokens are reported above, not gated. Completion excludes environment failures (v0.2.1 task Haiku
needed four retries after sessions were killed from outside).

| Task | Model | v0.2.1 floor | bare floor | v0.3.0 median | vs v0.2.1 | Completed (v0.3.0 · v0.2.1) | Result |
|------|-------|--------------|------------|---------------|-----------|-----------------------------|--------|
| task-2 | Sonnet 5.5 | 0.975 − 0.067 = 0.908 | 0.975 − 0.068 = 0.907 | 0.975 | ±0.000 | 5/5 · 5/5 | PASS |
| task-2 | Haiku 5.5 | 0.969 − 0.118 = 0.851 | 0.969 − 0.109 = 0.860 | 0.905 | −0.064 | 5/5 · 5/5 | PASS |
| task | Sonnet 5.5 | 0.946 − 0.135 = 0.811 | 0.971 − 0.085 = 0.886 | 0.946 | ±0.000 | 5/5 · 5/5 | PASS |
| task | Haiku 5.5 | 0.871 − 0.185 = 0.686 | 0.896 − 0.185 = 0.711 | 0.921 | +0.050 | 5/5 · 5/5 | PASS |

Every cell meets the quality condition. The gain condition is met on median overall in one cell
(task with Haiku); completion ties at 100% everywhere.

### Fidelity of unattended builds (Cycle 2 criteria 7–9)

Counted by [`scoring/fidelity.py`](scoring/fidelity.py) over every build's run directory: the
factory's own validator (`check_gate.py`) on the workspace, the commands the builder issued (Bash
tool calls in the session transcripts under `home/.claude/projects/`, not text it read), and the
verdict statement in `BENCHMARK.md`. `i-*` (harness `8f6a323`) is the last run before cycle 2, when
the same rules were prose; `j-*` is the published run.

| Measure | Bar | `i-*` (before) | `j-*` (published) |
|---------|-----|----------------|-------------------|
| Gate artifact at its canonical path | — | 13 of 20 | 20 of 20 |
| Validator passes (criterion 7) | ≥ 18 of 20 | 2 of 20 | 18 of 20 |
| Builds that issued `pkill` / `killall` / a pgrep-fed kill (criterion 8) | 0 | 1 of 20 | 1 of 20 |
| Builds killed from outside (criterion 8) | 0 | 0 | 0 |
| Sibling builds whose gate run that kill interrupted | — | 1 | 3 |
| Below-target verdicts | — | 6 | 4 |
| Below-target verdicts labelled shipped (criterion 9) | 0 | 3 | 0 |

- **Criterion 7 is met: 18 of 20 builds pass the validator** against a bar of 18. All 20 wrote the
  artifact at its canonical path: `uplift.json` from the manual protocol in all ten task builds and
  two task-2 Haiku builds, `plugin-eval.json` in the other eight task-2 builds. The two failures are
  those two task-2 Haiku builds; their artifact exists but their case record does not: Haiku 2's
  `data-split.yaml` records no `plugin_eval` cases, and Haiku 4 wrote `data-split.json` instead of
  `data-split.yaml`. Neither build ran the factory's gate: both bypassed `gate.py` and `check_gate.py`
  (Haiku 2 only read `gate.py --help`, then ran its own `e2e.sh` loop; Haiku 4 wrote `uplift.json`
  from its own batch scripts), neither built the 3.6 suite, and both still declared SHIP WITH
  CAVEATS. The factory after this run binds the artifact to a finished, hashed `gate.py` record and
  requires `check_gate.py`'s `GATE VALID:` line in `BENCHMARK.md` before any shipped verdict;
  `fidelity.py` now counts a shipped verdict with an invalid gate on its own.
- **Criterion 8 is not met.** One build, task-2 Haiku 3, started `gate.py` itself with `nohup … &`
  instead of through `scripts/run_owned.py`, then issued `pkill -f "gate.py --workspace"` and
  `pkill -f "claude plugin eval"` to restart it. That is the same 1-in-20 rate as `i-*`. Both
  patterns match every build's gate on the machine: within 8 seconds of the kill, the gate runs of
  task-2 Haiku 1, Haiku 5 and Sonnet 5 were interrupted. Each re-ran its gate and wrote a complete
  `plugin-eval.json`, and no session was killed, so no score changes. Their wall times include the
  re-run; Haiku 1's 1.9 hours is the longest build in the run.
- **Criterion 9 is met.** The three builds that exhausted their Phase 4 budget below the caveat band
  (task Haiku 1, 2 and 5) wrote verdict `UNSHIPPED` and call the output a draft. The fourth
  below-target build, task-2 Haiku 3, stopped on ITERATE with budget left (a data plateau, per 4.3)
  and wrote "do not publish as-is". None labels its output shipped. In `i-*`, task Haiku 5, task-2
  Haiku 4 and task-2 Haiku 5 ended on ITERATE and still called the output shipped.

Reproduce with `python3 scoring/fidelity.py "$BENCH_RUNS_ROOT"/j-*` (and `i-*`); offline, and
`python3 scoring/selftest-fidelity.py` must print `SELFTEST PASS`. An `UNSHIPPED` verdict is its own
label. For ITERATE and BLOCK the scanner reads only the verdict statement, so "the shipped file"
elsewhere in the report names the output file and does not count.

### Honest reading

- **The criterion only rules out large regressions.** With n=5 the pooled spreads are 0.067–0.185,
  so the floors sit between 0.686 and 0.908. A PASS means "no regression larger than run-to-run
  noise", not "as good as".
- **task-2 with Haiku is lower than v0.2.1:** median 0.905 against 0.969 (−0.064). It clears the
  pooled floor of 0.851, but every v0.3.0 build (best 0.953) is below v0.2.1's worst (0.958); `i-*`
  had 0.955 in this cell. The new loss is holdout h3's line items: the SKU check failed in 6 of 10
  executor runs across four of the five Haiku builds, against 0 of 10 for bare, skill-creator and
  v0.2.1, and 2 of 10 in `i-*`. Two builds also lost compliance points (scripts not referenced from
  SKILL.md; one of them hard-codes master data).
- **v0.3.0 vs v0.2.1: tied in two cells, below in one, above in one.** Tied on task-2 with Sonnet
  (0.975) and task with Sonnet (0.946). Above on task with Haiku (0.921 vs 0.871), where v0.3.0 also
  has the widest range in the table (0.761–0.946).
- **v0.3.0 beats bare in one cell only:** task with Haiku (0.921 vs 0.896). It ties bare on task-2
  with Sonnet and is below it on task-2 with Haiku (0.905 vs 0.969) and task with Sonnet (0.946 vs
  0.971). v0.2.1 ties bare on both task-2 cells and is below it on both task cells. Today's models
  reverse-engineer a near-perfect skill from a handful of gold standards in under five minutes; a
  discriminating task needs a rule surface too large to infer from a few examples.
- **v0.3.0 is slower than v0.2.1 in every cell:** 3.54× on task-2 Sonnet, 2.82× on task-2 Haiku,
  2.21× on task Sonnet and 1.62× on task Haiku. Input tokens are 2.77×, 1.50×, 1.83× and 1.41×
  v0.2.1's in the same order; output tokens 1.63×, 1.04×, 1.58× and 1.69×. It is also slower than
  `i-*` in three of four cells. v0.2.1 has no eval gate and v0.3.0 makes it mandatory; this run did
  not measure how much of the extra time the gate takes.
- **`skill-creator` alone is the cheapest non-bare arm**, and it lands at or below bare on every cell.
- **Every arm sometimes misses task-2's software-spend approval floor.** The h5 approval check is
  the most-failed check in each arm: 8 of 20 h5 executor runs for v0.3.0 and v0.2.1, 10 of 20 for
  bare and for skill-creator.

**The transcripts behind the fidelity counts are not published**, nor those behind the h5 and h3
failure counts and the holdout-leak search below. The `j-*` session transcripts under the builds'
isolated HOMEs alone are 2,840 files (226 MB), and they record the maintainer's machine paths and
environment. The
scripts that derive these claims are published (`scoring/fidelity.py`, the per-check results each
scorer writes to `score-report.json`, and the holdout search terms below), so a re-run of the matrix
reproduces the method on fresh transcripts.

**Earlier v0.3.0 runs** stay in `scores.csv`, superseded by `j-*`. `m-*-factory-v030-*` is the
first candidate; it regressed task-2 Haiku (0.932) because builds trusted the stale written policy
over the gold standards. `f-*` added the "gold standards win conflicts" rule (task-2 Haiku 0.969,
task Haiku 0.871); several of its sessions were killed and retried, and its times are not used. `g-*`
added the tribunal round-1 fixes (mandatory gate, git isolation, metric contract); all ten task-2
Sonnet sessions, retries included, were killed from outside (exit 143) and never scored. `h-*`
(harness `5f7fd3c`) added the rule that the factory stops only background jobs whose PIDs it
captured, and ran clean. Its medians were 0.960 (task-2 Sonnet), 0.937 (task-2 Haiku), 0.946 (task
Sonnet) and 0.971 (task Haiku), and 17 of 20 builds wrote a gate artifact.

`i-*` (harness `8f6a323`) added the tribunal round-2 fixes: a baseline commit before Phase 4;
user-invoked skills and unavailable plugin eval go to a specified manual with/without protocol
(`uplift.json`); the panel refuses to start without an uplift artifact; leave-one-out gate deltas are
labelled in-sample. Its medians were 0.975 (task-2 Sonnet), 0.955 (task-2 Haiku), 0.971 (task Sonnet)
and 0.886 (task Haiku), and it passed the release criterion in every cell.

`j-*` (harness `5ca4fa0`) replaces the gate and process prose with bundled scripts (`gate.py`,
`check_gate.py`, `run_owned.py`) and makes an exhausted budget a terminal `UNSHIPPED` state. Against
`i-*` its medians are equal on task-2 Sonnet, −0.050 on task-2 Haiku, −0.025 on task Sonnet and
+0.035 on task Haiku. At n=5 this run cannot separate the cycle-2 changes from run-to-run variance.

### Protocol notes (disclosed)

- **Executor change.** July used Haiku 4.5 at temperature 0; Haiku 5.5 rejects temperature and, with
  thinking on, spent the 512-token budget thinking and returned nothing. Thinking is off, and an empty
  truncated reply now throws instead of scoring 0. July and October numbers are not comparable.
- **Environment failures.** A Claude Code self-update (2.1.300 → 2.1.301) killed every live session
  once. Other builds' machine-wide `pkill -f` cleanup (`pkill -f "plugin eval"`, `pkill -f until`)
  killed more. Each killed session was retried once and never scored itself. Every session now runs
  with `DISABLE_AUTOUPDATER=1`. The factory starts and stops background jobs through its bundled
  `scripts/run_owned.py`; one `i-*` build and one `j-*` build still issued pattern kills (see
  Fidelity above).
- **Every retry** (all rows are in `results/runs.csv`; killed attempts carry `env_failure=1`):
  - v0.2.1 (`m-*`): task Haiku 2, 3, 4 and 5, and task-2 Haiku 2 were killed (exit 143). The killed
    attempts are kept as `-killed` rows and the retries ran under the original run-id.
  - `f-*`: task Sonnet 1, 2 and 3, and task-2 Haiku 2 and 3, retried under `-retry`.
  - `g-*`: task-2 Sonnet 1 to 5, retried under `-retry`; every retry was killed too (exit 143), so the
    cell was never scored.
  - `ar-*` (factory development experiments, not a published arm): `ar-e1-1`, `ar-e1-2` and `ar-e2-2`
    were killed; their `-killed` rows are kept, the retries ran under the original run-id.
  - `h-*`, `i-*` and `j-*`: no retries.
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
builder transcript, session log and build directory of the October runs was searched for holdout paths
(`task-2/holdout`, `task/holdout`, `answer-key.json`, `request-h*.md`): none found.
`SCORE_ONLY=1` re-scores an existing run.

Prereqs: Node 18+, Python 3, the `claude` CLI, and a headless credential as above.
