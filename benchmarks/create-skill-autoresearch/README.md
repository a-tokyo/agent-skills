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

Builders Claude Sonnet 5.5 and Haiku 5.5, n=5 per cell, medians with min–max. v0.3.0 is the `i-*`
run (factory at harness commit `8f6a323`): 20 of 20 builds completed, none killed from outside, no
retries. Baselines are `m-*`. Per-run rows in [`results/scores.csv`](results/scores.csv) and
[`results/runs.csv`](results/runs.csv). Environment failures (sessions killed from outside,
`env_failure=1`) are retried once and excluded from every number here.

| Task | Model | bare | skill-creator | factory v0.2.1 | **factory v0.3.0** |
|------|-------|------|---------------|----------------|--------------------|
| task-2 | Sonnet 5.5 | 0.975 (0.932–0.980) | 0.964 (0.908–0.975) | 0.975 (0.933–0.980) | **0.975 (0.975–1.000)** |
| task-2 | Haiku 5.5 | 0.969 (0.937–0.980) | 0.950 (0.943–0.980) | 0.969 (0.958–0.989) | **0.955 (0.892–0.989)** |
| task | Sonnet 5.5 | 0.971 (0.886–0.971) | 0.921 (0.921–0.971) | 0.946 (0.836–0.971) | **0.971 (0.946–0.971)** |
| task | Haiku 5.5 | 0.896 (0.846–0.921) | 0.896 (0.846–0.921) | 0.871 (0.836–0.943) | **0.886 (0.846–0.946)** |

Median build cost per arm: wall time · input tokens · output tokens. Input counts uncached, cache-write
and cache-read tokens summed over every transcript of the build, subagents included
([`arms/sum-tokens.mjs`](arms/sum-tokens.mjs)); each column's median is taken on its own.

| Task | Model | bare | skill-creator | factory v0.2.1 | factory v0.3.0 |
|------|-------|------|---------------|----------------|----------------|
| task-2 | Sonnet 5.5 | 2.5 min · 0.24M · 18k | 6.6 min · 0.37M · 22k | 8.2 min · 3.12M · 81k | 23.5 min · 5.18M · 102k |
| task-2 | Haiku 5.5 | 4.2 min · 0.75M · 51k | 4.0 min · 0.81M · 50k | 12.2 min · 7.92M · 194k | 22.6 min · 14.65M · 474k |
| task | Sonnet 5.5 | 0.6 min · 0.12M · 4k | 0.7 min · 0.18M · 4k | 9.3 min · 1.78M · 46k | 8.3 min · 1.94M · 44k |
| task | Haiku 5.5 | 0.7 min · 0.11M · 7k | 2.4 min · 0.42M · 12k | 17.6 min · 4.08M · 96k | 31.0 min · 6.87M · 119k |

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
| task-2 | Haiku 5.5 | 0.969 − 0.097 = 0.872 | 0.969 − 0.097 = 0.872 | 0.955 | −0.014 | 5/5 · 5/5 | PASS |
| task | Sonnet 5.5 | 0.946 − 0.135 = 0.811 | 0.971 − 0.085 = 0.886 | 0.971 | +0.025 | 5/5 · 5/5 | PASS |
| task | Haiku 5.5 | 0.871 − 0.110 = 0.761 | 0.896 − 0.100 = 0.796 | 0.886 | +0.015 | 5/5 · 5/5 | PASS |

Every cell meets the quality condition. The gain condition is met on median overall in both task
cells; completion ties at 100% everywhere.

### Honest reading

- **The criterion only rules out large regressions.** With n=5 the pooled spreads are 0.067–0.135,
  so the floors sit between 0.761 and 0.908. A PASS means "no regression larger than run-to-run
  noise", not "as good as".
- **v0.3.0 vs v0.2.1: tied on one cell, below on one, above on two.** Tied on task-2 with Sonnet
  (0.975 both), and v0.3.0's worst build (0.975) equals v0.2.1's median. Below on task-2 with Haiku
  (0.955 vs 0.969, −0.014): inside the pooled spread, but under v0.2.1's worst build (0.958). Above
  on task with Sonnet (0.971 vs 0.946) and task with Haiku (0.886 vs 0.871). The task Haiku gain is
  small, and both arms' ranges overlap almost entirely.
- **v0.3.0 never beats bare.** It ties bare on task-2 with Sonnet (0.975) and task with Sonnet
  (0.971). It is below bare on task-2 with Haiku (0.955 vs 0.969) and task with Haiku (0.886 vs
  0.896). v0.2.1 ties bare on both task-2 cells and is below it on both task cells. Today's models
  reverse-engineer a near-perfect skill from a handful of gold standards in under five minutes; a
  discriminating task needs a rule surface too large to infer from a few examples.
- **v0.3.0 is slower than v0.2.1 in three of four cells:** 2.87× on task-2 Sonnet, 1.86× on task-2
  Haiku and 1.76× on task Haiku. It is faster on task Sonnet (0.89×). Input tokens are 1.66×, 1.85×,
  1.09× and 1.69× v0.2.1's in the same order (task-2 Sonnet, task-2 Haiku, task Sonnet, task Haiku).
  Output tokens are 1.26×, 2.44×, 0.96× and 1.25×. v0.2.1 has no eval gate and v0.3.0 makes it
  mandatory; this run did not measure how much of the extra time the gate takes.
- **`skill-creator` alone is the cheapest non-bare arm**, and it lands at or below bare on every cell.
- **Every arm sometimes misses task-2's software-spend approval floor.** The h5 approval check is
  the most-failed check in each arm. It failed in 8 of 20 h5 executor runs for v0.3.0 and v0.2.1,
  and in 10 of 20 for bare and for skill-creator.

### Fidelity of unattended builds (known limitation)

- **Gate artifact at the canonical path: 13 of 20 builds.** SKILL.md 5.0 says the gate is never
  skipped, and the panel (5.2) does not start without `work/evaluation/plugin-eval.json` or
  `uplift.json`. Seven builds wrote `plugin-eval.json`: all task-2, covering five Sonnet and two Haiku
  builds. Six wrote the manual protocol's `uplift.json`: all task, covering three Sonnet and three
  Haiku builds. Task's skill is user-invoked, so 5.0 routes it to the manual protocol. A seventh task
  build (Haiku 3) wrote its `uplift.json` under `benchmarks/conventional-commits/` instead, so it does
  not count. The other six also ran a with/without comparison and finished with a `BENCHMARK.md`, but
  kept the result as logs, `uplift.txt` files or `BENCHMARK.md` prose: task Haiku 1, task Sonnet 1 and
  3, and task-2 Haiku 2, 3 and 5.
- **Strict gate validity: 2 of 20 builds.** `scoring/fidelity.py` runs the factory's own validator
  (`check_gate.py`, added after this run) on each workspace. It requires the artifact to cover exactly
  the cases recorded under `plugin_eval` in `data-split.yaml`, with 3 complete runs per arm. Only
  task-2 Sonnet 1 and 2 pass. The other eleven artifacts fail on the record, not the measurement. Nine
  builds recorded no flat case list: an empty list, a `cases:` mapping, or nothing. One
  recorded bare numbers (`1`…`7`) against cases named `gold-01`…; another recorded `input-01` against
  an artifact keyed `input/input-01.md`. The text these builds ran did not ask for that record in a
  checkable form; cycle 2's `gate.py` writes it.
- **Below-target verdict labelled shipped: 3 of 20 builds.** Task Haiku 5, task-2 Haiku 4 and task-2
  Haiku 5 ended on ITERATE, and their `BENCHMARK.md` still calls the output shipped.
- **Pattern kill: 1 of 20 builds.** The factory text these builds ran (pipeline-phases "Owned
  processes") required stopping only background jobs by captured PID, and forbade `pkill`, `killall`
  or `pgrep | xargs kill`. One task-2
  Sonnet build still ran `pkill -f "claude plugin eval"` to restart its own gate. That command also
  interrupted a sibling build's gate run: its partial `plugin-eval.json` records
  `partialReason: interrupted` at the same second. The sibling re-ran the gate and wrote a complete
  file. Every build still exited 0, so no score is affected. The count covers commands the builders
  issued (Bash tool calls in the session transcripts under `home/.claude/projects/`), not mentions
  in text they read.

Unattended runs follow the factory text closely but not perfectly; the counts above are what this run
did, not what the text promises. Reproduce them with `python3 scoring/fidelity.py
"$BENCH_RUNS_ROOT"/i-*` (offline; `python3 scoring/selftest-fidelity.py` must print `SELFTEST PASS`).

**The transcripts behind these counts are not published**, nor those behind the h5 failure counts and
the holdout-leak search below. The `i-*` builder and executor sessions alone are 1,553 session files
(256 MB), and they record the maintainer's machine paths and environment. The scripts that derive
these claims are published (`scoring/fidelity.py`, and the holdout search terms below), so a re-run of
the matrix reproduces the method on fresh transcripts.

**Earlier v0.3.0 runs** stay in `scores.csv`, superseded by `i-*`. `m-*-factory-v030-*` is the
first candidate; it regressed task-2 Haiku (0.932) because builds trusted the stale written policy
over the gold standards. `f-*` added the "gold standards win conflicts" rule (task-2 Haiku 0.969,
task Haiku 0.871); several of its sessions were killed and retried, and its times are not used. `g-*`
added the tribunal round-1 fixes (mandatory gate, git isolation, metric contract); all ten task-2
Sonnet sessions, retries included, were killed from outside (exit 143) and never scored. `h-*`
(harness `5f7fd3c`) added the rule that the factory stops only background jobs whose PIDs it
captured, and ran clean. Its medians were 0.960 (task-2 Sonnet), 0.937 (task-2 Haiku), 0.946 (task
Sonnet) and 0.971 (task Haiku), and 17 of 20 builds wrote a gate artifact.

`i-*` (harness `8f6a323`) adds the tribunal round-2 fixes:

- a baseline commit before Phase 4;
- user-invoked skills and unavailable plugin eval go to a specified manual with/without protocol
  (`uplift.json`);
- the panel refuses to start without an uplift artifact;
- leave-one-out gate deltas are labelled in-sample.

Because of the routing change, every task build in `i-*` used the manual protocol, while six `h-*`
task builds ran plugin eval on a user-invoked skill, which measures nothing. From `h-*` to `i-*`,
three cells rose (+0.015, +0.018, +0.025). Task with Haiku fell from 0.971 to 0.886, so the large
gain `h-*` showed there did not reproduce. At n=5 this run cannot separate the round-2 changes from
run-to-run variance.

### Protocol notes (disclosed)

- **Executor change.** July used Haiku 4.5 at temperature 0; Haiku 5.5 rejects temperature and, with
  thinking on, spent the 512-token budget thinking and returned nothing. Thinking is off, and an empty
  truncated reply now throws instead of scoring 0. July and October numbers are not comparable.
- **Environment failures.** A Claude Code self-update (2.1.300 → 2.1.301) killed every live session
  once. Other builds' machine-wide `pkill -f` cleanup (`pkill -f "plugin eval"`, `pkill -f until`)
  killed more. Each killed session was retried once and never scored itself. Every session now runs
  with `DISABLE_AUTOUPDATER=1`. The `i-*` factory text required stopping background jobs only by
  captured PID, and one `i-*` build still broke that rule (see Fidelity above); the factory now starts
  and stops them only through its bundled `scripts/run_owned.py`.
- **Every retry** (all rows are in `results/runs.csv`; killed attempts carry `env_failure=1`):
  - v0.2.1 (`m-*`): task Haiku 2, 3, 4 and 5, and task-2 Haiku 2 were killed (exit 143). The killed
    attempts are kept as `-killed` rows and the retries ran under the original run-id.
  - `f-*`: task Sonnet 1, 2 and 3, and task-2 Haiku 2 and 3, retried under `-retry`.
  - `g-*`: task-2 Sonnet 1 to 5, retried under `-retry`; every retry was killed too (exit 143), so the
    cell was never scored.
  - `ar-*` (factory development experiments, not a published arm): `ar-e1-1`, `ar-e1-2` and `ar-e2-2`
    were killed; their `-killed` rows are kept, the retries ran under the original run-id.
  - `h-*` and `i-*`: no retries.
- Factory arms ran unattended; builds sometimes skip steps and say so in their own reports.

## Results — July 2026 (v0.1/v0.2, task only)

Builders `claude-sonnet-5` and `claude-haiku-4-5`, n=2 (n=1 factory-Haiku), executor Haiku 4.5 at
temperature 0. Overall medians: bare 0.896 / 0.928, skill-creator 0.896 / 0.846, factory 0.896 / 0.896
(Sonnet / Haiku). No overall uplift; the task sat at the models' floor. Rows are kept in `scores.csv`.

## Reproduce

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
