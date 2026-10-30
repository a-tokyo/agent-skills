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

**task-3** — `freight-quote-builder` ([`task-3/brief.md`](task-3/brief.md)). Turn a free-text
shipment request into a parcel quote JSON by rating it across carriers against replaceable rate
tables: volumetric and billable weight, zone rate bands, six surcharges with per-carrier units and
thresholds, fuel, per-charge currency conversion, business-day delivery dates, and the choice of
carrier and service. The guide's format part is current; its rules part is two years old, and ten of
its rules are contradicted by the eight gold quotes, which are desk-log summaries (no base, fuel or
surcharge amounts). Five holdouts use other rate tables and vary what every training table holds
constant (rounding and size thresholds, surcharge units, holiday countries, zone postcode ranges,
box-weight limits). [`task-3/tools/generate.py`](task-3/tools/generate.py) proves every stale rule
changes a gold quote, every rule changes a holdout score, and no other combination of the stale and
naive rule variants reproduces all eight gold logs.

**Task-3 does not yet qualify under Amendment 6** (bare median ≤ 0.70 on both models). Bare-arm
probes, one build per model per design iteration, 2026-10-30:

| Design iteration (run ids) | bare Sonnet 5.5 | bare Haiku 5.5 |
|----------------------------|-----------------|----------------|
| 1 — stale rules, full gold quotes (`t3probe-*`) | 1.000 | 0.932 |
| 2 — + holdout-only regimes documented in the format guide (`t3i2probe-*`) | 0.933 | 0.921 |
| 3 — + desk-log gold, heavy-box rule settled (`t3i3probe-*`) | 1.000 | 0.985 |

Both models reproduce the gold quotes in 2–6 minutes and implement every rule the guide states. Their
only misses were one script bug and one rule that no gold quote or guide sentence settled, and that
rule was then fixed. Rows are in `results/` on this branch only.

Holdouts never reach a builder in any task.

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

- **task-3** — [`scoring/score-task3.mjs`](scoring/score-task3.mjs): task-2's agentic executor
  harness (imported: isolation, retry, env-failure rule), given one holdout `shipment.md` plus the
  holdout `rates/`, must write `quote.json` — 12 checks (carrier, service, billable kg, base,
  surcharge codes and amounts, fuel, total within 0.01, delivery date, exception codes and details).
  execution 0.80 · compliance 0.15 · craft 0.05, fixed before any task-3 run: compliance and craft are
  near-saturated for every arm, so execution carries the separation. One executor run costs about what
  a task-2 run does (Haiku 5.5, about $0.003, 6–8 turns, measured on h2 for each).

No LLM judge anywhere. `node scoring/selftest.mjs`, `node scoring/selftest-task2.mjs` and
`node scoring/selftest-task3.mjs` must print `SELFTEST PASS` before any score is trusted. The task-3
selftest also scores offline baselines: the reference solver 1.000, a skill that trusts the stale
rules 0.587, and a gold-fitter that reproduces all eight gold logs with constants and shortcuts 0.613.
A live run of `node scoring/score-task3.mjs scoring/fixtures/t3-good-skill --runs 2` scored 1.000 on
2026-10-30. A live run of `node scoring/score-task2.mjs
scoring/fixtures/t2-good-skill` (the task-2 reference solver wrapped as a skill) scored 1.000, and of
`scoring/fixtures/t2-bad-skill` (a generic skill) 0.22, on 2026-10-15.

**Why this scorer, and what the factory uses where:** [EVALUATORS.md](EVALUATORS.md) compares it with
`claude plugin eval` (regex and LLM graders) and skill-creator's eval on six task-2 skills. It was the
most stable and cheapest, but measures absolute quality only; plugin eval with deterministic graders is
the factory's 5.0 uplift gate, and skill-creator's lenient grader is read as a ranking.

## Results — v0.3.0 at n=10 (`n10-*`, October 2026)

This is the published result. The factory under test is harness `f3bf3b1`, byte-identical to
[`skills/create-skill-autoresearch/`](../../skills/create-skill-autoresearch/) in this branch: after the
run, the copy installed in all 44 v0.3.0 run directories (`plugins/create-skill-autoresearch`, env-failed
attempts included) was diffed against `f3bf3b1` and matched, and the copy in all 42 v0.2.1 run
directories matched agent-skills `99652ee`. Builders were Claude Sonnet 5.5 and Haiku 5.5 under Claude
Code, ten builds per arm and cell, v0.3.0 (`n10-*-factory-v030-*`) against v0.2.1
(`n10-*-factory-v021-*`), with the factory loaded with `--plugin-dir` so its kill guard and finish guard
were active, and every builder-launched `claude` call metered through the build's shim. bare is the `m-*`
baseline (n=5, an earlier run series). "commit messages" is `task`, "purchase orders" is `task-2`.

Per-build rows are in [`results/`](results/): `runs.csv` (every attempt, env failures marked
`env_failure=1`), `scores.csv`, `costs.csv` (from [`scoring/cost.py`](scoring/cost.py)) and
`fidelity.jsonl` (from [`scoring/fidelity.py`](scoring/fidelity.py)). Every number below is computed from
those files; environment failures are never scored.

### What was scored

40 builds per arm were planned. 39 v0.3.0 and 38 v0.2.1 builds are scored, and every scored build has
one row in each of `scores.csv`, `costs.csv` and `fidelity.jsonl`:

- **Four clean builds are unscored**: `v030` commit-message Haiku 8-retry and 10, and `v021`
  commit-message Haiku 7 and Sonnet 3. Each build exited 0 with `env_failure=0`, but the fixed scoring
  executor then failed on both attempts (`fetch failed`, or a 120 s timeout) and `score.txt` records
  `EXECUTOR FAULT … not scored`. They were not re-scored, so they have no score, cost or fidelity row.
- **v0.3.0 commit-message Haiku slot 9 is scored twice.** Its first attempt env-failed; `9-retry`
  then finished clean (0.921), and the rerun list still included slot 9, which finished clean again under
  the original id (0.946). Both rows are kept. So that cell's nine rows cover eight distinct slots, and
  v0.3.0's 39 rows cover 38 of 40 slots. Dropping either slot-9 row moves the cell's cost median from
  10.04M to 9.89M and leaves criterion 17 at PASS. The `9-retry` run directory is no longer on disk, so its
  committed rows cannot be re-derived.
- **47 environment failures** (`env_failure=1` rows for `n10-*` in `runs.csv`) happened across the run,
  from network loss when the laptop lid closed. Each was retried (`-retry`) or rerun under its
  original id until it ran clean. None is scored.

The n=9 cells are therefore commit-message Haiku (both arms) and commit-message Sonnet v0.2.1.

### Cost (criterion 17)

Cost units approximate dollars in units of one fresh input token: input + 1.25 × cache writes + 0.1 ×
cache reads + 5 × output, summed over every session of a build (builder, subagents, executors) plus the
plugin-eval gate's reported `costUsd` converted at the model's input price. Criterion 17 (Amendment 4a)
passes a cell when v0.3.0's median is at most v0.2.1's median plus v0.2.1's own spread (max − min of the
v0.2.1 builds). Units in millions, from `results/costs.csv`:

| Cell | n v0.3.0 / v0.2.1 | v0.3.0 median | v0.2.1 median | Ratio | v0.2.1 spread | Bar | Criterion 17 |
|------|-------------------|---------------|---------------|-------|---------------|-----|--------------|
| commit messages, Haiku 5.5 | 9 / 9 | 10.04 | 3.43 | 2.92× | 11.06 | 14.49 | PASS |
| commit messages, Sonnet 5.5 | 10 / 9 | 2.65 | 2.57 | 1.03× | 3.07 | 5.64 | PASS |
| purchase orders, Haiku 5.5 | 10 / 10 | 10.10 | 6.17 | 1.64× | 22.53 | 28.70 | PASS |
| purchase orders, Sonnet 5.5 | 10 / 10 | 4.98 | 3.67 | 1.36× | 4.14 | 7.82 | PASS |

Median units by session class, v0.3.0 / v0.2.1 (each column's median taken on its own, so the classes do
not sum to the total):

| Cell | Builder | Subagents | Executors | Plugin-eval gate |
|------|---------|-----------|-----------|------------------|
| commit messages, Haiku | 4.29 / 2.51 | 4.85 / 0.37 | 0.33 / 0.13 | 0 / 0 |
| commit messages, Sonnet | 1.48 / 2.03 | 0.72 / 0.20 | 0.52 / 0.00 | 0 / 0 |
| purchase orders, Haiku | 6.63 / 3.02 | 1.93 / 0.41 | 0.52 / 2.16 | 0.28 / 0 |
| purchase orders, Sonnet | 2.36 / 0.86 | 1.09 / 0.37 | 1.30 / 2.42 | 0.28 / 0 |

- **v0.3.0 is not cheaper than v0.2.1 in any cell.** Its median build costs 1.03× v0.2.1's on
  commit-message Sonnet, 1.36× and 1.64× on purchase orders, and 2.92× on commit-message Haiku. Criterion
  17 passes on Amendment 4a's bar (v0.2.1's median plus v0.2.1's own spread), not on literal parity: the
  bar is wide because v0.2.1's own builds vary widely (Haiku spreads of 11.06M and 22.53M).
- **Commit-message Haiku is panel-heavy.** Its subagent median (4.85M) exceeds its builder median; the
  v0.3.0 panel spawns a median of 9 subagents per build against 3 for v0.2.1, and a post-panel fix
  re-runs the panel in full (Amendment 4b). That cell has no plugin-eval gate cost because its gates ran
  in manual mode, metered as executor sessions.
- **One row was corrected before publishing.** `n10-t2-factory-v021-haiku-1` had been metered while one
  executor session was still being written (20 sessions in the row, 21 on disk, the last written after
  `score.txt`). Re-metered from its run directory it is 3.95M, not 3.06M. The cell's median is unchanged
  and its bar drops from 28.86M to 28.70M.
- `python3 scoring/cost.py "$BENCH_RUNS_ROOT"/n10-* --compare n10-=n10-` reproduces the table from the
  run directories, except that it sees eight commit-message Haiku v0.3.0 builds (median 9.89M, ratio
  2.88×) because the `9-retry` directory is gone. The other 76 builds re-meter to their `costs.csv` row.
  Pass only the scored run ids (the env-failed attempts are directories too); `costs.csv` is the record.

### Quality (criterion 4)

Median overall (min–max) from `results/scores.csv`. Criterion 4 (Amendment 2) passes a cell when v0.3.0's
median is at least v0.2.1's median minus the pooled spread of both arms, and at least bare's median minus
the pooled spread of v0.3.0 and bare.

| Cell | bare (`m-*`, n=5) | v0.2.1 | **v0.3.0** | vs v0.2.1 | v0.2.1 floor | bare floor | Criterion 4 |
|------|-------------------|--------|------------|-----------|--------------|------------|-------------|
| commit messages, Sonnet 5.5 | 0.971 (0.886–0.971) | 0.946 (0.886–0.971) | **0.971 (0.886–0.971)** | +0.025 | 0.861 | 0.886 | PASS |
| commit messages, Haiku 5.5 | 0.896 (0.846–0.921) | 0.921 (0.811–0.971) | **0.871 (0.836–0.971)** | −0.050 | 0.761 | 0.761 | PASS |
| purchase orders, Sonnet 5.5 | 0.975 (0.932–0.980) | 0.975 (0.912–1.000) | **0.976 (0.932–1.000)** | +0.001 | 0.887 | 0.907 | PASS |
| purchase orders, Haiku 5.5 | 0.969 (0.937–0.980) | 0.959 (0.838–1.000) | **0.925 (0.867–0.978)** | −0.033 | 0.797 | 0.856 | PASS |

Both Sonnet cells are at or above v0.2.1 and bare. **Both Haiku cells are below v0.2.1** (−0.050 and
−0.033) and below bare (−0.025 and −0.043). Every one of those gaps is inside its pooled spread (0.160 and
0.162 against v0.2.1), so they are not measured regressions, but neither is there a measured gain: at
n=10 this is quality parity on Sonnet and a non-significant shortfall on Haiku.

### Fidelity (criteria 7, 8, 9, 12, 13)

Counted by `fidelity.py` over each scored build: the factory's own validator (`check_gate.py`) on the
workspace, the shell commands the builder issued (from its session transcripts, not text it read), and
the verdict in `BENCHMARK.md`. v0.2.1 has no gate, receipts or finish check, so its gate rows are "no
gate" by construction, not failures of a check it was asked to pass.

| Measure | Bar | v0.3.0 (39 rows) | v0.2.1 (38 rows) |
|---------|-----|------------------|------------------|
| Valid gate (`check_gate.py`) | ≥ 90% | **38** | 0 (no gate) |
| `--finish` reports the build finished | — | 39 | 0 (no finish check exists) |
| Pattern-kill commands executed | 0 | **0** | 1 |
| Pattern-kill attempts blocked by the guard | — | 1 | 0 |
| Builds the finish guard sent back · send-backs | — | 5 · 7 | n/a |
| Shipped verdicts on an invalid gate | 0 | **0** | 25 (no valid gate) |
| Verdicts above the label the panel consensus allows | 0 | **0** | n/a |
| Below-target verdicts labelled shipped (mislabels) | 0 | **0** | 1 |
| Verdicts | — | 20 SHIP WITH CAVEATS · 19 `UNSHIPPED` | 23 SHIP WITH CAVEATS · 2 SHIP · 4 ITERATE · 9 none |

- **The one invalid v0.3.0 gate** is purchase-order Haiku 10: `GATE INVALID: work/evaluation/data-split.yaml:
  plugin_eval is not recorded` (`no_split`), no gate artifact and no panel consensus. It labelled itself
  `UNSHIPPED`, which `--finish` accepts, so nothing shipped on it. The same build made the run's only
  pattern-kill attempt, which the kill guard blocked (0 executed).
- **v0.2.1's kill** is commit-message Haiku 10 (`pkill -f run-uplift.sh; pkill -f
  "work/evaluation/evaluate.sh"`). Its mislabel is purchase-order Haiku 3, which wrote ITERATE where
  `fidelity.py` allows no ship label. Nine v0.2.1 builds wrote no verdict at all.
- **`UNSHIPPED` rate: 19 of 39 v0.3.0 builds (49%)**: commit messages Sonnet 2 of 10, Haiku 6 of 9,
  purchase orders Sonnet 3 of 10, Haiku 8 of 10. Every `UNSHIPPED` sits on an ITERATE, ESCALATE or BLOCK
  consensus (or, for Haiku 10, no consensus). `UNSHIPPED` is the panel declining to ship, not a low score:
  the scored skills of `UNSHIPPED` builds are inside the same ranges as the shipped ones. v0.2.1 has no
  such state, and its 25 shipped verdicts rest on no validated gate.

### Wall time

Median wall time per build, v0.3.0 / v0.2.1, from `runs.csv` over the scored builds: commit messages
Sonnet 11 / 21 min, Haiku 26 / 154 min; purchase orders Sonnet 32 / 10 min, Haiku 43 / 17 min. Wall time
is **not reliable in this run**: `wall_seconds` includes time the laptop slept with the lid closed (the
longest scored build reads 72,014 s, about 20 hours, and v0.2.1 commit-message Haiku's 154-minute median
is inflated the same way). Read the medians as rough and the maxima not at all; wall time is not gated.

### Disclosures

- **Builder model: use Sonnet.** Sonnet is the recommended builder. Haiku builds end `UNSHIPPED` in 6 of 9
  (commit messages) and 8 of 10 (purchase orders) runs, against 2 of 10 and 3 of 10 for Sonnet, cost
  1.6–2.9× v0.2.1, and score below v0.2.1 and bare (inside the spread).
- **The six fixes after this run are not benchmarked.** The final panel's fixes (harness `6b040c5` to
  `db3a28e`: the kill guard acts only inside a factory build, a settled gate is reused only with every
  option unchanged, the loop guard ends a below-target plateau on the measured noise threshold, manifest
  paths with spaces are read, panel receipts grep their evidence quotes, OpenCode's guard gets its cwd) are
  covered by the offline tests, the kill-guard smoke and live Cursor smokes, not by a new matrix. The
  plateau rule can end a loop sooner, and the quote check can fail a receipt that cited a command's output.
- **`tests/` ships inside the skill folder.** An `npx skills` or plugin install receives
  `tests/` (about 3,300 lines of offline tests); Claude Code reads only the files SKILL.md references, so they
  cost install size, not context.
- **Guard coverage per host.** Only Claude Code has the full guard set verified live (kill, loop, subagent and
  finish guards; this benchmark ran there). Cursor: kill guard verified live as a plugin, subagent guard
  offline-tested, finish guard offline only (`cursor-agent -p` runs no `stop` hook). Codex: kill, loop and
  finish guards contract-tested offline, untested in Codex; no subagent guard. OpenCode: kill, loop and finish
  plugins tested with node, untested in OpenCode; no subagent guard. Every guard acts only inside a factory
  build ([hosts.md](../../skills/create-skill-autoresearch/references/hosts.md)).
- **Run store and earlier data.** Run directories lived in `~/.cache/csa-bench` until 2026-10-24, when
  that directory was lost; the run directories of every run before that date (`u-*` and the Amendment 4 cost
  runs up to `fm-*`/`vm-*` included) are gone, and their published numbers can no longer be re-derived
  from transcripts. Since then the run store is outside the
  cache, and `cost.py` and `fidelity.py` append each build's row to `results/` right after scoring.
- **Metering history.** Executor calls a builder made with `claude -p --no-session-persistence` left no
  session log and were invisible to `cost.py` until the build's `claude` shim (which drops the flag and
  logs the call) went in before the `fm-*`/`vm-*` runs; cost numbers published before that undercount
  executors in both arms. Executors called straight against an API with curl or an SDK are still not
  counted; they would leave no session log in either arm.
- **The transcripts behind these counts are not published.** They record the maintainer's machine paths
  and environment. The scripts that derive every number are published, so a re-run reproduces the method.

Reproduce from the workspace's benchmarks directory (the matrix driver lives there, not in this repo)
with `FACTORY_V030_DIR=<factory> caffeinate -dimsu ./drive-matrix.sh cells-n10-v030.txt [concurrency]`
and `cells-n10-v021.txt` for v0.2.1, then the `-rerun` lists for env-failed builds. A single build is
`arms/run-and-score.sh factory <model> <run-id> [task-2]` (see Reproduce).

## Earlier runs (superseded by `n10-*`)

Each of these measured an earlier factory revision; their rows stay in `results/`. The run directories
of every run before 2026-10-24 were lost with `~/.cache/csa-bench` (see Disclosures), and cost figures
from before `fm-*`/`vm-*` predate complete metering.

- **Cost runs (Amendment 4, `x-*` to `fp-*`, 2026-10-20 to 10-28).** On `u-*` against `m-*`, criterion 17
  failed on both Haiku cells (8.57M against a 4.99M bar, 22.64M against 9.17M) under the old metering.
  `x-*`/`y-*` compared a full against a scoped post-panel re-check (full kept, Amendment 4b), `z-*`
  measured phase workers (reverted, 4c), `fm-*`/`vm-*` were the first completely metered v0.3.0 / v0.2.1
  pair, and `fn-*` and `fp-*` metered later v0.3.0 revisions before this run.
- **`u-*` (harness `f684797`, n=5).** The previous published result. Criterion 4 passed in all cells
  (medians: purchase orders Sonnet 1.000, Haiku 0.917; commit messages Sonnet 0.946, Haiku 0.871); 20 of 20
  gates valid, 20 of 20 verdicts (13 SHIP WITH CAVEATS, 7 `UNSHIPPED`), no kill command. Two Cursor builds
  (no `claude` on PATH) both scored 0.971 with valid gates, showing the pipeline runs without Claude Code.
- **`s-*` (harness `82ec859`, n=5).** Criterion 4 passed in all cells; 19 of 20 gates valid (purchase-order
  Haiku 4 ran no gate and wrote `UNSHIPPED`), which the finish guard was then added to catch.
- **`r-*` (harness `8e505b0`, n=5) and the interim `q-*`.** 20 of 20 gates valid; eight verdicts covered
  a skill the panel had scored before a last fix, which panel binding (`f4c31cc`) then closed.
- **`p-*` (cycle 3, harness `0c94f94`, n=5).** 19 of 20 gates valid; three Haiku builds ended after the
  premortem without a verdict, which made Phase 5 run to a verdict in one turn.
- **`k-*` (cycle 2, harness `a3fa032`, n=5).** The first run with the bundled gate scripts: 19 of 20 gates
  valid against 0 of 20 for the prose-era `i-*` and `j-*` builds on the same validator, and pattern kills
  fell from one build to none.
- **`m-*-factory-v030`, `f-*`, `g-*`, `h-*`, `i-*`, `j-*`.** Development candidates. `m-*` regressed
  purchase-order Haiku by trusting the stale written policy, which `f-*`'s "gold standards win conflicts"
  rule fixed; `g-*` was killed from outside and never scored; `j-*` replaced the gate prose with scripts.

### Protocol notes (disclosed)

- **Executor change.** July used Haiku 4.5 at temperature 0; Haiku 5.5 rejects temperature and, with
  thinking on, spent the 512-token budget thinking and returned nothing. Thinking is off, and an empty
  truncated reply now throws instead of scoring 0. July and October numbers are not comparable.
- **Environment failures before `n10-*`.** A Claude Code self-update (2.1.300 → 2.1.301) killed every
  live session once, and other builds' machine-wide `pkill -f` cleanup killed more (`m-*`, `f-*`, `g-*`).
  Every session now runs with `DISABLE_AUTOUPDATER=1`, and killed attempts carry `env_failure=1` in
  `runs.csv`, retried under `-retry` or the original id.
- Factory arms run unattended; builds sometimes skip steps and say so in their own reports.

## Results — July 2026 (v0.1/v0.2, task only)

Builders `claude-sonnet-5` and `claude-haiku-4-5`, n=2 (n=1 factory-Haiku), executor Haiku 4.5 at
temperature 0. Overall medians: bare 0.896 / 0.928, skill-creator 0.896 / 0.846, factory 0.896 / 0.896
(Sonnet / Haiku). No overall uplift; the task sat at the models' floor. Rows are kept in `scores.csv`.

## Reproduce

The factory's bundled scripts (`gate.py`, `check_gate.py`, `run_owned.py`) and their offline tests run
on macOS, Linux and WSL; the harness `checks` workflow runs them on `ubuntu-latest` and `macos-latest`.
At harness `f3bf3b1`, `npm test` passes 103 of 103 structural checks and 205 script tests (GATE PASS).

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
builder transcript, session log and build directory of the October runs, `k-*` to `u-*` and `n10-*` included, was searched for holdout paths
(`task-2/holdout`, `task/holdout`, `answer-key.json`, `request-h*.md`): none found. (One `p-*` build, task Haiku 5,
named its own gold-derived key `work/evaluation/answer-key.json`; its contents differ from both holdout keys.)
`SCORE_ONLY=1` re-scores an existing run.

Prereqs: Node 18+, Python 3, the `claude` CLI, and a headless credential as above.
