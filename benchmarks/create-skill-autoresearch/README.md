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
hook that blocks `pkill`, `killall` and pgrep-fed kills — is active, and from `u-*` on so is its finish
guard, a `Stop` hook that sends an unfinished build back once; the skill is then listed as
`create-skill-autoresearch:create-skill-autoresearch`. Earlier `factory` runs and `factory-v021` place
the skill under the fake HOME's `~/.claude/skills/` instead. [`arms/smoke-kill-guard.sh`](arms/smoke-kill-guard.sh)
is a one-call check that the guard blocks a dummy `pkill` under that load. Its single run on
2026-10-18 (Haiku 5.5, Claude Code 2.1.301) passed: the agent got the guard's message as a tool error,
the session continued, and `fidelity.py` counted 0 kill commands and 1 blocked attempt.

`HOST=cursor arms/run-and-score.sh factory sonnet <run-id> [task-2]` runs the same build under
`cursor-agent -p --force --trust --output-format stream-json` (Sonnet 5.5 = `claude-sonnet-5-5-high`;
`CURSOR_MODEL` overrides). The skills go in the workspace's `.agents/skills/`, the kill guard runs as a
project `beforeShellExecution` hook in `.cursor/hooks.json`, and the build's PATH holds no `claude`
(checked before the run; `meta.json` records `host`, `claude_on_path` and `home_isolated`). cursor-agent
keeps its login in the macOS login Keychain, so on macOS the arm now isolates HOME as the Claude arm does,
with one symlink (`Library/Keychains`) carrying the login: the maintainer's `~/.cursor`, `~/.claude` and
`~/.agents` skills, rules, plugins and hooks are out of reach. Cursor's own bundled skills and any
team-managed plugins still load, since they come with the account. Off macOS the arm keeps the maintainer's
HOME (`home_isolated: false`). Tokens come from the transcript's final
`result` event (the top-level session; subagent usage is not broken out). `fidelity.py` reads the
stream-json transcript's shell calls, counting a hook-rejected one as blocked.

Smoke build `a3-t1-factory-cursor-sonnet-1` (task, 2026-10-19, harness `6a9f6f2`, cursor-agent
2026.10.16): exit 0 in 2549 s, overall **0.921** (compliance 1.000, craft 0.667, execution 0.952),
7.46M input / 79k output tokens, `meta.json` `claude_on_path: false`. Its gate validated in manual mode
(`uplift.json`, 6 gold cases, mean Δ +0.329, `input-05` at Δ <= 0) with `cursor-agent` as the executor,
research and panel ran as Cursor subagents, and it ended `UNSHIPPED` (training 0.813 below the 0.85
target), which `check_gate.py --finish` accepts. One build is a smoke test, not a result; its token cells
were filled from `meta.json` after a separator bug in the cursor branch left them empty. It ran before
HOME isolation, with the maintainer's HOME: `~/.claude/skills` (the builder read the synced `tribunal`
skill's `SKILL.md` there) and `~/.cursor` skills and rules were visible to it. Its prompt also named the
`cursor-agent` command to use as executor and judge; the arm's prompt now says only to use the host's
headless CLI as the factory describes, so `references/hosts.md` has to carry that.

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
  non-zero without writing `po.json` is retried once. A second such run is an env failure for that
  case — excluded from the execution mean and reported in `score-report.json`, never scored 0 — only
  when its transcript carries an infrastructure signature (the API/network patterns `arms/run-arm.sh`
  uses, an auth error, or claude failing to spawn); without one, a timeout or a non-zero exit is the
  skill's result and scores 0. That positive-signature rule dates from the release head; before it a
  non-zero exit alone was an env failure, but no published task-2 report (151 rows, all with
  `score-report.json`) excluded a run, so it changes no number. The scorer gained the env-failure rule after the `j-*` run; there, the only executor
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

## Results — v0.3.0 release (`u-*`, October 2026)

The published v0.3.0 result is measured on the factory this PR ships: harness `f684797`, byte-identical
to [`skills/create-skill-autoresearch/`](../../skills/create-skill-autoresearch/). After the run, the
copy installed in each of the 22 builds (`plugins/` for Claude Code, `ws/.agents/skills/` for Cursor) was
diffed against it and matched. The run repeats all 20 `s-*` cells: builders Claude Sonnet 5.5 and Haiku
5.5 under Claude Code 2.1.304, n=5 per cell, with the factory loaded with `--plugin-dir`, so the kill
guard and the finish guard were active. Tables show medians with min–max, from the `overall` column of
[`results/scores.csv`](results/scores.csv). All 20 builds exited 0 and produced a skill. There were no
environment failures (`env_failure=0` in [`results/runs.csv`](results/runs.csv)), no retries and no
executor faults. bare, skill-creator and v0.2.1 are the `m-*` baselines. Two more task builds ran
under Cursor (below) and are not in these tables.

| Task | Model | bare | skill-creator | factory v0.2.1 | **factory v0.3.0** |
|------|-------|------|---------------|----------------|--------------------|
| task-2 | Sonnet 5.5 | 0.975 (0.932–0.980) | 0.964 (0.908–0.975) | 0.975 (0.933–0.980) | **1.000 (0.944–1.000)** |
| task-2 | Haiku 5.5 | 0.969 (0.937–0.980) | 0.950 (0.943–0.980) | 0.969 (0.958–0.989) | **0.917 (0.872–0.968)** |
| task | Sonnet 5.5 | 0.971 (0.886–0.971) | 0.921 (0.921–0.971) | 0.946 (0.836–0.971) | **0.946 (0.896–0.971)** |
| task | Haiku 5.5 | 0.896 (0.846–0.921) | 0.896 (0.846–0.921) | 0.871 (0.836–0.943) | **0.871 (0.761–0.946)** |

Median build cost: wall time · input tokens · output tokens, counted as in the cycle-2 table below.
v0.2.1 excludes its killed attempts. These figures are reported, not gated.

| Task | Model | factory v0.2.1 | factory v0.3.0 (`u-*`) | v0.3.0 ÷ v0.2.1 (wall · in · out) |
|------|-------|----------------|------------------------|-----------------------------------|
| task-2 | Sonnet 5.5 | 8.2 min · 3.12M · 81k | 40.0 min · 7.17M · 114k | 4.90× · 2.30× · 1.41× |
| task-2 | Haiku 5.5 | 12.2 min · 7.92M · 194k | 61.4 min · 48.87M · 548k | 5.04× · 6.17× · 2.82× |
| task | Sonnet 5.5 | 9.3 min · 1.78M · 46k | 15.1 min · 3.41M · 85k | 1.62× · 1.91× · 1.85× |
| task | Haiku 5.5 | 17.6 min · 4.08M · 96k | 27.9 min · 9.77M · 352k | 1.58× · 2.40× · 3.68× |

### Release criterion (criterion 4, Amendment 2)

In each cell, v0.3.0's median must be at least v0.2.1's median minus the pooled spread (max − min over
both arms' runs in the cell). It must also be at least bare's median minus the pooled spread of v0.3.0
and bare. On top of that, v0.3.0 must gain in median overall or completion in at least one cell.

| Task | Model | v0.2.1 floor | bare floor | v0.3.0 median | vs v0.2.1 | Completed (v0.3.0 · v0.2.1) | Result |
|------|-------|--------------|------------|---------------|-----------|-----------------------------|--------|
| task-2 | Sonnet 5.5 | 0.975 − 0.067 = 0.908 | 0.975 − 0.068 = 0.907 | 1.000 | +0.025 | 5/5 · 5/5 | PASS |
| task-2 | Haiku 5.5 | 0.969 − 0.117 = 0.852 | 0.969 − 0.108 = 0.861 | 0.917 | −0.052 | 5/5 · 5/5 | PASS |
| task | Sonnet 5.5 | 0.946 − 0.135 = 0.811 | 0.971 − 0.085 = 0.886 | 0.946 | ±0.000 | 5/5 · 5/5 | PASS |
| task | Haiku 5.5 | 0.871 − 0.185 = 0.686 | 0.896 − 0.185 = 0.711 | 0.871 | ±0.000 | 5/5 · 5/5 | PASS |

Every cell meets the quality condition. The gain condition is met in one cell: task-2 with Sonnet,
+0.025 over both v0.2.1 and bare, inside the 0.067 and 0.068 pooled spreads, so it counts for the
criterion but is not a measured improvement. Task with Sonnet and task with Haiku tie v0.2.1 and sit
0.025 below bare. Task-2 with Haiku is 0.052 below both, inside the 0.117 and 0.108 spreads.
Completion ties at 5/5 everywhere.

### Run-to-run noise across the v0.3.0 runs

Five v0.3.0 runs measured the same 20 cells, each on a later factory revision (`k-*` `a3fa032`, `p-*`
`0c94f94`, `r-*` `8e505b0`, `s-*` `82ec859`, `u-*` `f684797`). The changes between them mostly target
the gate, the panel and how a build ends; few touch how the produced skill is written.

| Task | Model | `k-*` | `p-*` | `r-*` | `s-*` | **`u-*`** | Range of the five medians | `u-*` spread (max − min) |
|------|-------|-------|-------|-------|-------|-----------|---------------------------|--------------------------|
| task-2 | Sonnet 5.5 | 0.964 | 0.957 | 0.975 | 0.980 | **1.000** | 0.043 | 0.056 |
| task-2 | Haiku 5.5 | 0.969 | 0.958 | 0.969 | 0.932 | **0.917** | 0.052 | 0.096 |
| task | Sonnet 5.5 | 0.971 | 0.946 | 0.971 | 0.971 | **0.946** | 0.025 | 0.075 |
| task | Haiku 5.5 | 0.871 | 0.886 | 0.921 | 0.921 | **0.871** | 0.050 | 0.185 |

From `s-*` to `u-*` the medians moved +0.020, −0.015, −0.025 and −0.050 (rows in table order). Each
move is smaller than the spread inside the `u-*` cell itself, and the five medians of a cell span
0.025–0.052. Release-to-release movement of this size is run-to-run noise at n=5, in either direction.

### Fidelity (criteria 7, 8, 9, 12, 13)

These counts come from `python3 scoring/fidelity.py "$BENCH_RUNS_ROOT"/u-*`, run with the validator
published beside this README (byte-identical to the one the builds ran), and from `check_gate.py
--finish` on each workspace. How `fidelity.py` counts is explained under **Fidelity of unattended
builds** in the cycle-2 section below. The `s-*` column is as published, counted with `82ec859`'s
validator, which had no role receipts, consensus or finish guard.

| Measure | Bar | `s-*` Claude Code | **`u-*` Claude Code** | `u-*` Cursor |
|---------|-----|-------------------|-----------------------|--------------|
| Builds | — | 20 | 20 | 2 |
| Gate artifact at its canonical path | — | 19 | 20 | 2 |
| Validator passes (criterion 7) | ≥ 18 of 20 | 19 | **20** | 2 |
| "artifact cases differ" failures (criterion 12) | 0 | 0 | **0** | 0 |
| Stale gates · shipped verdicts on an invalid gate | — | 0 · 0 | 0 · 0 | 0 · 0 |
| Pattern-kill commands executed (criteria 8, 13) | 0 | 0 | **0** | 0 |
| Pattern-kill attempts blocked by the guard | — | 0 | 0 | 0 |
| Builds killed from outside | 0 | 0 | 0 | 0 |
| Builds that ended without a verdict (no `BENCHMARK.md`) | — | 0 | **0** | 0 |
| Below-target verdicts (`UNSHIPPED`) | — | 6 | 7 | 1 |
| Below-target verdicts labelled shipped (criterion 9) | 0 | 0 | **0** | 0 |
| Three role receipts and a consensus (`work/experiments/panel/`) | — | — | 20 | 2 |
| Verdicts above the label the consensus allows | 0 | — | **0** | 0 |
| Builds that ran `check_gate.py --finish` themselves | — | 19 | 20 | 2 |
| `--finish` today reports `BUILD FINISHED` | — | 19 | 20 | 2 |
| Builds the finish guard sent back · send-backs | — | — | 6 · 14 | n/a (no stop hook in `-p`) |

- **Criterion 7 is met: 20 of 20.** The task builds gated in manual mode (`uplift.json`, 6 gold cases,
  mean Δ +0.422 to +0.767). Of the task-2 gates, six used `claude plugin eval` (all five Sonnet builds
  and Haiku 2; 10 or 11 cases, 7 or 8 of them gold) and four used manual mode (Haiku 1, 3, 4 and 5; 7
  gold cases). Two task-2 Haiku gates list gold cases at Δ <= 0 (Haiku 2: `gold-02`, `gold-07`; Haiku 5:
  one case), which caps both at SHIP WITH CAVEATS, the verdict they wrote.
- **Criterion 12 is met:** there was no "artifact cases differ" failure.
- **Criteria 8 and 13 are met, but the kill guard is still untested here.** No build executed or
  attempted a pattern kill, so the guard blocked nothing. The smoke test (see Arms, above) remains the
  evidence that it blocks.
- **Criterion 9 is met.** The seven below-target Claude Code builds wrote `UNSHIPPED`: task Sonnet 2
  and 5, task Haiku 2, 3 and 5, and task-2 Haiku 1 and 4. They include the four lowest-scoring builds
  (task Haiku 3 0.761, task Haiku 5 0.786, task Haiku 2 0.871, task-2 Haiku 4 0.872). The other 13
  verdicts are SHIP WITH CAVEATS. None is a plain SHIP.
- **The verdict follows the panel's scores.** Each panel role wrote a receipt after scoring, and
  `check_gate.py --consensus` computed the label from the three receipts. Every verdict equals the
  label its consensus allows: the 13 shipped verdicts sit on a SHIP WITH CAVEATS consensus, and the
  seven `UNSHIPPED` ones on ITERATE (five) or ESCALATE (two). In `s-*` the panel receipt was written
  before the panel ran, and `--finish` read no score.
- **What an `UNSHIPPED` report shows.** `--finish` requires the current `GATE VALID:` and `PANEL
  CONSENSUS:` lines in an `UNSHIPPED` `BENCHMARK.md`, and all eight (Cursor included) carry both, so
  each states its gate Δ (+0.413 to +0.980, all positive) and the panel's label, overall and target.
  Only two (task Sonnet 2 and Cursor 2) put the lines directly under `Verdict: UNSHIPPED`; the others
  put them 6 to 63 lines further down. No `UNSHIPPED` build had a blocked dimension (`blocked: none` in
  all eight): ITERATE came from a panel overall of 0.634–0.737 against the 0.85 target, and ESCALATE
  from the Devil's Advocate. The consensus line names no limiting dimension in that case, so the reason
  is in each report's prose (task-2 Haiku 1, for example, names the approval tier as its binding
  constraint). `UNSHIPPED` is a panel verdict, not a score: task Sonnet 5 (0.946) and Cursor 2 (0.971)
  are among them.
- **The finish guard sent builds back 14 times, in 6 builds** (task Haiku 5: 2, task Sonnet 3 and 4:
  1 each; task-2 Haiku 1: 6, Haiku 4: 3, Haiku 5: 1). Thirteen send-backs were "no `BENCHMARK.md` at
  the build root" and one was a `BENCHMARK.md` without a `Verdict:` line. All six builds then wrote a
  verdict that `--finish` accepts. The guard never sends a build back twice in a row: in Claude Code a
  stop that follows a send-back (`stop_hook_active`) always ends the turn. Task-2 Haiku 1's six
  send-backs are six turns, not a loop. It ran its panel as background subagents, and each subagent's
  completion notice opened a new turn. The builder ended each of those turns while the rest of the
  panel was still running, before `BENCHMARK.md` existed. Each send-back was followed by an allowed
  stop, all six fell between 23:16 and 23:23 UTC, and the last turn wrote `UNSHIPPED` (consensus
  ITERATE) and ended at 23:24 with `--finish` passing, after 999 s, the fastest task-2 Haiku build.

### Cursor builds (Amendment 3)

`u-t1-factory-cursor-sonnet-1` and `-2` ran the task cell with `HOST=cursor` (Sonnet 5.5 =
`claude-sonnet-5-5-high`) on the same factory. `meta.json` records `home_isolated: true` and
`claude_on_path: false` for both. Both scored **0.971** (compliance 1.000, craft 1.000, execution
0.952), above the Claude Code Sonnet median of 0.946. Both gates validated in manual mode (`uplift.json`,
6 gold cases, mean Δ +0.623 and +0.883, no case at Δ <= 0), each with `composer-2.5` as the executor the
builder chose, so their gate numbers do not compare with the Claude Code builds'. Both wrote three role
receipts and a consensus and ran and passed `--finish` themselves. The finish guard was not active:
`cursor-agent -p` runs no `stop` hook (`references/hosts.md`). Build 1 ended SHIP
WITH CAVEATS on a panel overall of 0.814. Build 2 ended `UNSHIPPED` on an ESCALATE consensus (overall
0.723). Wall times were 59 and 39 minutes, with 12.63M / 77k and 7.36M / 61k tokens (top-level session
only). Two builds show that the pipeline runs end to end without Claude Code; they are not a quality
result for Cursor.

### Honest reading

- **No regression beyond noise, and no gain beyond it either.** Task-2 Sonnet improves (1.000, +0.025
  over v0.2.1 and bare, with three of five builds at 1.000). Both task cells tie v0.2.1 and sit 0.025
  below bare. Task-2 Haiku is below both. Every gap is inside its pooled spread, and the `s-*` to `u-*`
  movement is inside the run-to-run noise shown above. These tasks are near the models' ceiling (see
  the cycle-2 reading below).
- **Honest negative: task-2 Haiku is the weakest cell for the second run in a row.** Its median is
  0.917 (runs 0.903, 0.917, 0.968, 0.872 and 0.958, Haiku 1 to 5), against 0.969 for v0.2.1 and bare and
  0.932 in `s-*`. The approval tier is the main loss: 14 of 25 holdout scores miss it, against 11 in
  `s-*` and 1 for `u-*` Sonnet. Haiku 1 and 4 also lose holdout h3 almost entirely (0.385 and 0.462:
  wrong lines, quantities and totals). Two builds hard-code master data and two leave a script
  unreferenced (compliance). The gate cannot see these failures, because it measures uplift on the gold
  standards; the factory labelled Haiku 1 and 4 `UNSHIPPED`.
- **Task Haiku's craft checks are the other loss.** Three of five builds score craft 0.333 (a
  multi-line description and trigger scaffolding), and Haiku 3 and 5 fail most checks on holdout h3.
- **Reliability is complete in this run.** 20 of 20 gates valid, 20 of 20 verdicts, no kill command,
  no stale gate, no below-target build labelled shipped, and no verdict above the panel's consensus.
  The `s-*` gap (task-2 Haiku 4 ended without a gate) did not recur, and the six builds the finish
  guard sent back all ended with a verdict `--finish` accepts.
- **v0.3.0 costs more in every cell, and more than in `s-*`**: wall time is 1.58×–5.04× v0.2.1's and
  input tokens 1.91×–6.17×. Task-2 Haiku costs most (48.9M median input tokens against 30.5M in `s-*`;
  three builds above 45M).
- **Weakest builds:** task Haiku 3 (0.761: craft 0.333, execution 0.768) and task Haiku 5 (0.786).

Reproduce from the workspace's benchmarks directory (the matrix driver lives there, not in this repo)
with `FACTORY_V030_DIR=<factory> caffeinate -dimsu ./drive-matrix.sh cells-v030-u-final.txt 6`. Run the
two Cursor builds with `HOST=cursor` and `cells-v030-u-cursor.txt`. `<factory>` is
`skills/create-skill-autoresearch` at this branch's head (byte-identical to harness `f684797`). A single
build is `arms/run-and-score.sh factory <model> <run-id> [task-2]` (see Reproduce).

### Changes measured by `u-*` (harness `0c94f94`..`f684797`)

The published result matches the shipped factory: `u-*` measured all of the changes below, and the
factory has not changed since.

`s-*` (harness `82ec859`) to `u-*` (`f684797`):
- **Verdict from panel role receipts** (`9dcdeab`): each panel role writes a receipt after scoring
  (`check_gate.py --panel-receipt`), bound to the skill and rubric hashes, and `--consensus` computes the
  label from the three receipts and `references/verdict-rules.json`. `--finish` rejects a verdict above
  that label, and an `UNSHIPPED` report carries the gate and consensus lines. A missed should-trigger or
  a firing near miss invalidates the gate, and the gate fails a run whose inputs changed meanwhile.
- **Finish guard** (`34c587e`): a turn-end hook (Claude Code and Codex `Stop`, Cursor `stop`, OpenCode
  `session.idle`) runs `--finish` in a factory build workspace and sends an unfinished build back once.
- **Kill guard and processes**: a variable kill is blocked only when a listing filled that variable
  (`9730fb4`), and a job stopped mid-exit is reported as stopped (`44e1a18`).
- **Text and CI** (`1f28210`, `f684797`): SKILL.md 5.2–5.4 and `references/hosts.md` document the
  receipts, consensus and finish guard per host, and the Windows gate smoke runs them.

The changes from `p-*` to `s-*` are listed under the `s-*` run below.

### After `u-*` (harness `f684797`..`8c7f7e2`, not re-benchmarked)

The final review's fixes change how invalid input is rejected, not how a valid build runs, so `u-*` was
not rerun (user decision). Offline tests, CI on Windows and macOS, and a live kill-guard smoke cover them:
- **Revised panel scores are range-checked** (`0f52f52`): a synthesis receipt must keep each revised
  score on the rubric scale and back an extreme score with evidence, as first-round scores must.
- **Listing plus kill in one call is blocked** (`8f53dc1`), so a PID list renamed or redirected through a
  file no longer slips past the guard. A kill of literal PIDs still passes. The guard is a tripwire for
  honest mistakes, not a sandbox; `run_owned.py` and the written rule are the contract.
- **The Claude Code hooks fail open** (`295fc2c`, `onFailure: continue`), so a hook that cannot start on
  Windows without Git Bash no longer blocks every tool call.
- **Docs** (`e8df77d`): the version goes in `metadata.version` across SKILL.md and the references, the
  Windows note says the factory (not Claude Code) needs Git Bash, and the executor echo is labelled a
  format check. consensus-protocol.md states that `--panel-receipt` checks extreme-score evidence is present,
  not that it is verbatim; the orchestrator's grep stays the verbatim check.
- **Benchmark** (`af8cfcb`): the task-2 executor's whole process group is killed on timeout.
- **Coverage** (workspace): criterion 15's row B13 re-quoted to the `metadata.version` rule;
  `coverage_rows.py --check` exits 0 and is part of the release verification commands.

The live smoke (`arms/smoke-kill-guard.sh`, Claude Code 2.1.304, haiku) loaded the hooks with
`onFailure` set, blocked a `pkill` and a `killall`, and ran the next command. Replaying the 48 `u-*` synthesis
receipts through the new range check rejects none of their 105 revised scores.

### Code diet (harness `eb9830d`..`5c8e7ae`, not re-benchmarked)

Native Windows is dropped (Amendment 5, user decision 2026-10-21): the factory runs on macOS, Linux and WSL,
and on native Windows every script exits early with a pointer to WSL. The Job Object, msvcrt, Git Bash and
`taskkill` code paths, their tests and the `windows-latest` CI jobs are gone; the gate smoke now runs on
`ubuntu-latest` and the script tests on `macos-latest`. The kill guard keeps its POSIX rules and drops the
`taskkill`/`Stop-Process`/`wmic` patterns. Scripts and hooks shrink from 3,616 to 2,781 lines: `run_owned.py`'s
supervisor forks the command itself instead of starting a wrapper process, and the docstrings point at the
references instead of restating them. Validator codes, messages and the owned-process guarantees are
unchanged; 184 offline tests, the gate smoke and the live kill-guard smoke (`arms/smoke-kill-guard.sh`) pass.

## Release run on `82ec859` (`s-*`, October 2026), superseded by `u-*`

This run used the factory at harness `82ec859`, loaded with `--plugin-dir`, with n=5 per cell. All 20
Claude Code builds exited 0, with no environment failures and no retries. Median overall (min–max):
task-2 Sonnet 0.980 (0.935–1.000), task-2 Haiku 0.932 (0.858–0.980), task Sonnet 0.971
(0.921–0.971), task Haiku 0.921 (0.736–0.971). Criterion 4 passed in every cell, and the gain
condition was met in three (+0.005, +0.025, +0.050), all inside the spread. Median wall time was 33.0,
55.3, 10.7 and 34.9 minutes in the same order. Fidelity: 19 of 20 gates were valid. The miss, task-2
Haiku 4, ran neither the gate nor the validator, wrote `UNSHIPPED` and never ran `--finish`; the finish
guard (`34c587e`) answers that end state. There were no kill commands and no below-target build
labelled shipped. Its panel receipt was written before the panel ran and held no score, so one shipped
verdict (task-2 Haiku 2) rested on panel scores that predated its last two fixes; role receipts
(`9dcdeab`) close that gap. Two Cursor builds both scored 0.971 and ended `UNSHIPPED` and SHIP WITH
CAVEATS. The driver was `cells-v030-s-final.txt`.

`r-*` (harness `8e505b0`) to `s-*` (`82ec859`):
- **Panel bound to the skill** (`f4c31cc`): `check_gate.py --panel` records the skill hash the panel
  scores in `work/experiments/panel.json`. `--finish` rejects a SHIP or SHIP WITH CAVEATS on any other
  hash.
- **Gate backend by host** (`ea30826`): `gate.py --backend plugin|manual`.
- **Gate checks**: every gold standard is gated when there are fewer than ten, and the test split
  starts at ten (`115f0ea`). `--skill` must be a name, not a path, and finishing needs 3 runs per arm
  (`b8d4f17`). Every `work/evaluation` file is a gate input (`ac1c311`).
- **Process and kill guard**: the factory signals only a group whose verified leader it started
  (`8cfb891`). Pattern kills are matched within one simple command (`912892d`), and a backslash-newline
  continuation is joined as bash does (`82ec859`). The Claude Code guard also covers PowerShell tool
  calls (`89a86cd`).
- **Text and CI**: the loop writes the factory's records and aggregates every METRIC (`055b714`), and
  the timing constants give their reasons (`e61ac10`). The Cursor plugin hook is labelled unsupported
  on native Windows (`0d39446`). CI runs one manual gate end to end on `windows-latest` (`12261a0`).

## Release-candidate run (`r-*`, October 2026), superseded by `s-*` and `u-*`

This run used the factory at harness `8e505b0`, loaded with `--plugin-dir`, with n=5 per cell. All 20
Claude Code builds exited 0, with no environment failures and no retries. Median overall (min–max):
task-2 Sonnet 0.975 (0.955–1.000), task-2 Haiku 0.969 (0.926–0.989), task Sonnet 0.971
(0.971–0.971), task Haiku 0.921 (0.886–0.971). Criterion 4 passed in every cell, and the gain condition
was met on task Sonnet (+0.025) and task Haiku (+0.050). Median wall time was 23.9, 53.4, 13.1 and 28.3
minutes in the same order.

Fidelity: 20 of 20 gates were valid and 20 of 20 builds wrote a verdict and ran `--finish`. There were
no kill commands and no below-target build labelled shipped. Eight verdicts (five of them shipped)
said the panel had scored the skill before a last fix and was not re-run. Panel binding (`f4c31cc`)
closes that gap. Two Cursor builds scored 0.921 and 0.871 and ended `UNSHIPPED`. The driver was
`cells-v030-r-final.txt`, with the factory at agent-skills `06f6be6` (byte-identical to harness
`8e505b0`).

### Interim check (`q-*`)

Ten Haiku builds (both tasks, n=5) ran on harness `fa60341`, before the 15 commits from `da9442a` to
`8e505b0` (Windows port, gate checks, kill-guard fixes). Medians were 0.946 (task) and 0.978 (task-2).
All 10 wrote a verdict and none executed a kill. With `fa60341`'s own validator, all 10 gates
validate. The validator published here reads six as stale (`stale_inputs`), three of them with a
shipped verdict. That is a validator change, not a build failure: five task-2 gates predate `a79b3b6`,
which extended the input hash to the whole wrapper plugin, so their `gate.json` holds no hash for
those files. Task Haiku 3 wrote `work/evaluation/check_gate.out` after its gate, a file the newer hash
set also covers. `r-*`, then `s-*` and `u-*`, replaced `q-*` as the result.

## Cycle-3 run (`p-*`, October 2026), superseded by `r-*`, `s-*` and `u-*`

The factory at harness `0c94f94` (cycle 3: `gate.py record`, the kill-guard hook, the evaluator
guidance, `check_gate.py --finish`), loaded with `--plugin-dir`, n=5 per cell. All 20 builds exited 0,
with no environment failures and no retries. Median overall (min–max): task-2 Sonnet 0.957
(0.932–1.000), task-2 Haiku 0.958 (0.908–0.989), task Sonnet 0.946 (0.821–0.971), task Haiku 0.886
(0.818–0.921). Criterion 4 passed in every cell. Its gain condition was met only by task Haiku's +0.015,
inside a 0.125 pooled spread, and v0.3.0 was below bare in all four cells, each inside the spread.
Median wall time was 38.1, 40.0, 15.7 and 17.5 minutes in the same order (4.66×, 3.28×, 1.68× and
0.99× v0.2.1).

Fidelity: 19 of 20 gates valid, no "artifact cases differ" failure, no kill command, no below-target
build labelled shipped. The invalid gate, task-2 Haiku 3, skipped 3.6 and 5.0 and still wrote
`UNSHIPPED`, which `--finish` at `0c94f94` accepted; since `6a9f6f2` that needs a logged gate attempt.
Three Haiku builds (task 4 and 5, task-2 5) ended their turn after the premortem without a verdict, the
same count and phase as `k-*`. Driver: `cells-v030-c3.txt`, with the factory at agent-skills `11b8269`
(byte-identical to harness `0c94f94`).

## Cycle-2 run (`k-*`, October 2026), superseded

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
worked gold case does the same (`references/phase-3.md`, `holdout-03`: `graders/approval.md`
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

- **The criterion only rules out large regressions.** With n=5 the pooled spreads were 0.036–0.157, so
  a PASS means "no regression larger than run-to-run noise", not "as good as".
- **v0.3.0 vs v0.2.1 and bare:** above v0.2.1 on task Sonnet (+0.025), tied in two cells, below on
  task-2 Sonnet (−0.011); above bare in no cell. Today's models reverse-engineer a near-perfect skill
  from a handful of gold standards in under five minutes; a discriminating task needs a rule surface too
  large to infer from a few examples.
- **Weakest cell:** task with Haiku, 0.871 (0.786–0.896), with two craft scores of 0.333.
- **Cost:** wall time 1.13×–4.07× v0.2.1 and input tokens 1.32×–2.31×; v0.2.1 has no eval gate.
  `skill-creator` alone is the cheapest non-bare arm and lands at or below bare on every cell.
- **Every arm sometimes misses task-2's software-spend approval floor.** The h5 approval check is the
  most-failed check in each arm: 8 of 20 h5 executor runs for v0.3.0 and for v0.2.1, 10 of 20 for bare
  and for skill-creator.

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
measured by the `p-*` run, then `r-*`, `s-*` and `u-*`):
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
failure counts and the holdout-leak search below (`k-*`, `p-*`, `r-*`, `s-*` and `u-*` alike). The `k-*` session transcripts under the builds'
isolated HOMEs alone are 2,473 files (about 204 MB), and they record the maintainer's machine paths
and environment. The scripts that derive these claims are published (`scoring/fidelity.py`, the
per-check results each scorer writes to `score-report.json`, and the holdout search terms below), so
a re-run of the matrix reproduces the method on fresh transcripts.

**Earlier v0.3.0 runs** stay in `scores.csv`, superseded by `u-*` (and before it `s-*`, `r-*`, `p-*` and `k-*`).
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
  - `h-*`, `i-*`, `j-*`, `k-*`, `p-*`, `q-*`, `r-*`, `s-*` and `u-*`: no retries.
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
builder transcript, session log and build directory of the October runs, `k-*`, `p-*`, `q-*`, `r-*`, `s-*` and `u-*` included, was searched for holdout paths
(`task-2/holdout`, `task/holdout`, `answer-key.json`, `request-h*.md`): none found. (One `p-*` build, task Haiku 5,
named its own gold-derived key `work/evaluation/answer-key.json`; its contents differ from both holdout keys.)
`SCORE_ONLY=1` re-scores an existing run.

Prereqs: Node 18+, Python 3, the `claude` CLI, and a headless credential as above.
