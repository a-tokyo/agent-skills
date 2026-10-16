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
`SELFTEST PASS` before any score is trusted; the task-2 reference solver wrapped as a skill scores
1.000 live, a generic skill 0.22.

## Results — October 2026 (v0.2.1 vs v0.3.0)

Builders Claude Sonnet 5.5 and Haiku 5.5, n=5 per cell, medians with min–max. Per-run rows in
[`results/scores.csv`](results/scores.csv) (`m-*` baseline and first v0.3.0 candidate, `f-*` final
v0.3.0); environment failures (sessions killed from outside) are retried once and never scored —
see [`results/runs.csv`](results/runs.csv).

| Task | Model | bare | skill-creator | factory v0.2.1 | **factory v0.3.0** |
|------|-------|------|---------------|----------------|--------------------|
| task-2 | Sonnet 5.5 | 0.975 (0.932–0.980) | 0.964 (0.908–0.975) | 0.975 (0.933–0.980) | **0.975 (0.969–1.000)** |
| task-2 | Haiku 5.5 | 0.969 (0.937–0.980) | 0.950 (0.943–0.980) | 0.969 (0.958–0.989) | **0.969 (0.937–0.989)** |
| task | Sonnet 5.5 | 0.971 (0.886–0.971) | 0.921 (0.921–0.971) | 0.946 (0.836–0.971) | **0.971 (0.861–0.971)** |
| task | Haiku 5.5 | 0.896 (0.846–0.921) | 0.896 (0.846–0.921) | 0.871 (0.836–0.943) | **0.871 (0.871–0.971)** |

Median build wall time, first v0.3.0 candidate vs v0.2.1 (the final run's times are inflated by host
sleep overnight and are not reported; the final differs from the candidate only by the three-line
conflicts rule):

| Task | Model | bare | skill-creator | factory v0.2.1 | factory v0.3.0 (candidate) |
|------|-------|------|---------------|----------------|----------------------------|
| task-2 | Sonnet 5.5 | 2.5 min | 6.6 min | 8.2 min | 6.6 min |
| task-2 | Haiku 5.5 | 4.2 min | 4.0 min | 12.2 min | 9.2 min |
| task | Sonnet 5.5 | 0.6 min | 0.7 min | 9.3 min | 7.5 min |
| task | Haiku 5.5 | 0.7 min | 2.4 min | 17.6 min | 22.5 min |

Every factory build completed (exit 0, skill produced) in both versions.

### Honest reading

- **Neither factory version beats the bare model on output quality.** Today's models reverse-engineer
  a near-perfect skill from a handful of gold standards in two to four minutes, on both tasks — task-2
  went through three hardening rounds (stale policy, number words, merges, exact exception details)
  and stayed saturated (bare 0.94–1.00 in pilots). At this task size the factory's value is not output
  uplift; a discriminating task needs a rule surface too large to infer from a few examples.
- **v0.3.0 does not regress v0.2.1** in any cell and is ahead in one: task with Sonnet (+0.025); the other three cells tie.
  It is faster than v0.2.1 in three of four cells and more consistent on task-2 (Sonnet spread 0.031
  vs 0.047).
- **The first v0.3.0 candidate did regress** task-2 Haiku (0.932): builds trusted the stale written
  policy over the gold standards. v0.3.0's "gold standards win conflicts" rule fixed it (0.969); the
  candidate's rows stay in `scores.csv` as `m-*-factory-v030-*`.
- **`skill-creator` alone is the cheapest non-bare arm** and lands slightly below bare on most cells.
- **Every arm misses task-2's software-spend approval floor sometimes** (holdout h5 is the most-failed
  check across all arms).

### Protocol notes (disclosed)

- **Executor change.** July used Haiku 4.5 at temperature 0; Haiku 5.5 rejects temperature and, with
  thinking on, spent the 512-token budget thinking and returned nothing. Thinking is off, and an empty
  truncated reply now throws instead of scoring 0. July and October numbers are not comparable.
- **Environment failures.** A Claude Code self-update (2.1.300 → 2.1.301) killed every live session
  once, and host sleep killed five more overnight. All were retried once under a `-retry` run-id;
  none were scored. Every session now runs with `DISABLE_AUTOUPDATER=1`.
- Factory arms ran unattended; builds sometimes skip optional steps (the plugin-eval gate ran in about
  half of v0.3.0 builds) and say so in their own reports.

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
