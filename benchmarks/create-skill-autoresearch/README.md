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
  third person, ≥ 3 script functions, a concrete example).

No LLM judge anywhere. `node scoring/selftest.mjs` and `node scoring/selftest-task2.mjs` must print
`SELFTEST PASS` before any score is trusted. A live run of `node scoring/score-task2.mjs
scoring/fixtures/t2-good-skill` (the task-2 reference solver wrapped as a skill) scored 1.000, and of
`scoring/fixtures/t2-bad-skill` (a generic skill) 0.22, on 2026-10-15.

## Results — October 2026 (v0.2.1 vs v0.3.0)

Builders Claude Sonnet 5.5 and Haiku 5.5, n=5 per cell, medians with min–max. v0.3.0 is the `h-*`
run (factory at harness commit `5f7fd3c`): 20 of 20 builds completed, none killed. Per-run rows in
[`results/scores.csv`](results/scores.csv) and [`results/runs.csv`](results/runs.csv); baselines
are `m-*`. Environment failures (sessions killed from outside, `env_failure=1`) are retried once and
excluded from every number here.

| Task | Model | bare | skill-creator | factory v0.2.1 | **factory v0.3.0** |
|------|-------|------|---------------|----------------|--------------------|
| task-2 | Sonnet 5.5 | 0.975 (0.932–0.980) | 0.964 (0.908–0.975) | 0.975 (0.933–0.980) | **0.960 (0.937–0.980)** |
| task-2 | Haiku 5.5 | 0.969 (0.937–0.980) | 0.950 (0.943–0.980) | 0.969 (0.958–0.989) | **0.937 (0.849–0.969)** |
| task | Sonnet 5.5 | 0.971 (0.886–0.971) | 0.921 (0.921–0.971) | 0.946 (0.836–0.971) | **0.946 (0.886–0.971)** |
| task | Haiku 5.5 | 0.896 (0.846–0.921) | 0.896 (0.846–0.921) | 0.871 (0.836–0.943) | **0.971 (0.921–0.971)** |

Median build cost per arm: wall time · input tokens · output tokens. Input counts uncached, cache-write
and cache-read tokens summed over every transcript of the build, subagents included
([`arms/sum-tokens.mjs`](arms/sum-tokens.mjs)); each column's median is taken on its own.

| Task | Model | bare | skill-creator | factory v0.2.1 | factory v0.3.0 |
|------|-------|------|---------------|----------------|----------------|
| task-2 | Sonnet 5.5 | 2.5 min · 0.24M · 18k | 6.6 min · 0.37M · 22k | 8.2 min · 3.12M · 81k | 24.6 min · 6.31M · 111k |
| task-2 | Haiku 5.5 | 4.2 min · 0.75M · 51k | 4.0 min · 0.81M · 50k | 12.2 min · 7.92M · 194k | 15.2 min · 7.41M · 188k |
| task | Sonnet 5.5 | 0.6 min · 0.12M · 4k | 0.7 min · 0.18M · 4k | 9.3 min · 1.78M · 46k | 12.9 min · 2.83M · 62k |
| task | Haiku 5.5 | 0.7 min · 0.11M · 7k | 2.4 min · 0.42M · 12k | 17.6 min · 4.08M · 96k | 22.4 min · 5.00M · 148k |

### Honest reading

- **v0.3.0 vs v0.2.1 is mixed on quality.** Better on task with Haiku (0.971 vs 0.871; v0.3.0's
  median is above v0.2.1's best build, 0.943). Tied on task with Sonnet (0.946 both). Lower on both
  task-2 cells: Sonnet 0.960 vs 0.975 (−0.015, inside v0.2.1's 0.933–0.980 spread) and Haiku 0.937
  vs 0.969 (−0.032, below v0.2.1's worst build, 0.958; one v0.3.0 build scored 0.849).
- **v0.3.0 is slower in every cell:** 3.0× v0.2.1 on task-2 Sonnet, 1.25× on task-2 Haiku, 1.4× on
  task Sonnet, 1.3× on task Haiku. Input tokens are 2.0×, 0.94×, 1.6× and 1.2× v0.2.1's; output
  tokens 1.4×, 0.97×, 1.35× and 1.5×. task-2 Haiku is the one cell where v0.3.0 used fewer tokens.
- **The mandatory uplift gate ran and is the main time cost.** 17 of 20 builds wrote an uplift
  artifact (12 `plugin-eval.json`, 5 the manual with/without `uplift.json`); v0.2.1 has no gate.
  From the first gate command to the last artifact write spans a median 64% of build wall time in
  those 17 (an upper bound: other work can overlap it). Two of the three builds without an artifact
  were the fastest, at 7–8 minutes.
- **Neither version beats bare, except v0.3.0 on task with Haiku.** v0.2.1 ties bare on task-2 and is below it
  on task. v0.3.0 is below bare on task-2 both models and task Sonnet, and above it on task Haiku
  (0.971 vs 0.896; v0.3.0's worst build equals bare's best, 0.921). Today's models reverse-engineer
  a near-perfect skill from a handful of gold standards in under five minutes; a discriminating
  task needs a rule surface too large to infer from a few examples.
- **`skill-creator` alone is the cheapest non-bare arm** and lands at or below bare on every cell.
- **Every arm misses task-2's software-spend approval floor sometimes:** the h5 approval check is
  the most-failed check, failed in 8–12 of each arm's 20 executor runs.

**Earlier v0.3.0 candidates** stay in `scores.csv`, superseded by `h-*`. `m-*-factory-v030-*` is the
first candidate; it regressed task-2 Haiku (0.932) because builds trusted the stale written policy
over the gold standards. `f-*` added the "gold standards win conflicts" rule (task-2 Haiku 0.969,
task Haiku 0.871); several of its sessions were killed and retried, and its times are not used. `g-*`
added the tribunal round-1 fixes (mandatory gate, git isolation, metric contract); all ten task-2
Sonnet sessions, retries included, were killed from outside (exit 143) and never scored.
`h-*` added the rule that the factory stops only background jobs whose PIDs it captured, and ran clean.

### Protocol notes (disclosed)

- **Executor change.** July used Haiku 4.5 at temperature 0; Haiku 5.5 rejects temperature and, with
  thinking on, spent the 512-token budget thinking and returned nothing. Thinking is off, and an empty
  truncated reply now throws instead of scoring 0. July and October numbers are not comparable.
- **Environment failures.** A Claude Code self-update (2.1.300 → 2.1.301) killed every live session
  once, and other builds' machine-wide `pkill -f` cleanup (`pkill -f "plugin eval"`, `pkill -f until`)
  killed more. Each was retried once under a `-retry` run-id and never scored. Every session now runs
  with `DISABLE_AUTOUPDATER=1`, and the factory stops only background jobs whose PIDs it captured.
- Factory arms ran unattended; builds sometimes skip steps and say so in their own reports.

## Results — July 2026 (v0.1/v0.2, task only)

Builders `claude-sonnet-5` and `claude-haiku-4-5`, n=2 (n=1 factory-Haiku), executor Haiku 4.5 at
temperature 0. Overall medians: bare 0.896 / 0.928, skill-creator 0.896 / 0.846, factory 0.896 / 0.896
(Sonnet / Haiku). No overall uplift; the task sat at the models' floor. Rows are kept in `scores.csv`.

## Reproduce

```bash
cd benchmarks/create-skill-autoresearch
node scoring/selftest.mjs && node scoring/selftest-task2.mjs   # offline: prove the probes discriminate
export ANTHROPIC_API_KEY=...                                   # or CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)

# one build + its score; task = conventional-commits, task-2 = purchase-order-builder
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
never touch the maintainer's real config or credentials. `SCORE_ONLY=1` re-scores an existing run.

Prereqs: Node 18+, Python 3, the `claude` CLI, and a headless credential as above.
