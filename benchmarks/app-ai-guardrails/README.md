# app-ai-guardrails — benchmark

Measures whether the `app-ai-guardrails` skill makes an AI coding agent scaffold a **greenfield**
repo with a live, toothed guardrail canon (lint / typecheck / test / coverage / build / e2e / audit,
all wired and green) versus a bare prompt with no skill. The metric is a deterministic
`guardrail_score` (0-100+) plus a binary `all_gates_pass`.

## Method

- **Two arms, same prompt.** `bare` (model only) and `with-skill` (model + the live
  `../../skills/app-ai-guardrails/SKILL.md`, installed as a user-level skill — see containment
  below) get the identical rendered prompt from `task/prompt-template.md` for a given stack
  (`task/stacks.json`). Only the skill's presence differs.
- **Fresh agent per run.** Each run is one `claude -p` invocation (`run-arm.sh`) in its own run
  directory; run-ids are one-shot (`runs/<id>/`, never reused). n=1-6 per cell (see "thin cells"
  below) at default temperature.
- **Deterministic scorer, no LLM.** `evaluate.sh` detects the stack + its 7 gate commands
  (`lib/detect-gates.mjs`), executes the tier-1 gates (`lint`, `typecheck`, `test`, `coverage`,
  `build`) in the scaffolded repo, and captures exit codes. `check-guardrails.mjs` owns all rubric
  logic (weights recorded in the workspace's `CRAFT-DECISIONS.md`, decision D11) and emits
  `METRIC guardrail_score=<n>` / `METRIC all_gates_pass=<0|1>` lines — a file read, a config parse,
  or a subprocess exit code, never a judgment call. The scorer exits 3 on its own internal error so
  a bug can never masquerade as a legitimate low score.
- **Teeth probes.** Beyond "does the gate exist and pass", the scorer mutates the repo (introduces a
  lint violation with a non-ignorable identifier, deletes the largest test file to blow a coverage
  floor, etc.) and checks the gate actually fails — report-only tooling that never fails is scored
  as if the gate were absent. This is what "teeth" means throughout the skill and this benchmark.
- **Containment.** Every run gets an **isolated `$HOME`** (`GIT_CONFIG_GLOBAL`, `XDG_*`, a per-run
  `~/.claude/skills/`) so a `--dangerously-skip-permissions` agent can never read or write the
  maintainer's real config, credentials, or caches; only package-manager caches are shared
  read-through. The skill is injected as a **user-level** skill inside that isolated `$HOME` (not
  inside the scaffolded repo and not at the run-root) — the only placement Claude Code actually
  discovers without contaminating the scored git tree (two other placements were tried and failed;
  see `CRAFT-DECISIONS.md` D20/D20-amendment in the source workspace). **One lane at a time**: two
  concurrent `claude` processes under the same account trip rate limiting that presents as an
  instant connection-close indistinguishable from a real failure — runs are driven sequentially per
  lane (`drive-cells.sh`). **Auth** is a headless credential exported into the isolated `$HOME`
  (`CLAUDE_CODE_OAUTH_TOKEN`, minted via `claude setup-token`, or `ANTHROPIC_API_KEY`) — the
  interactive login the CLI otherwise relies on lives in the real `$HOME`, which containment hides.

## Results (`node summarize.mjs`, medians)

| arm | model | stack | pm | n | median score | all-gates-pass |
|---|---|---|---|---|---|---|
| bare | haiku | next | npm | 5 | 26 | 0/5 |
| bare | opus | next | npm | 6 | 39.5 | 0/6 |
| bare | sonnet | django | uv | 3 | 10 | 0/3 |
| bare | sonnet | go | go | 3 | 4 | 0/3 |
| bare | sonnet | nest | npm | 3 | 30 | 0/3 |
| bare | sonnet | next | npm | 5 | 36 | 0/5 |
| bare | sonnet | rust | cargo | 3 | 7 | 0/3 |
| bare | sonnet | springboot | gradle | 3 | 7 | 0/3 |
| **with-skill** | haiku | next | npm | 3 | **94** | 3/3 |
| **with-skill** | opus | next | npm | 3 | **94** | 3/3 |
| **with-skill** | sonnet | django | uv | 4 | **91** | 4/4 |
| **with-skill** | sonnet | go | go | 1 | **95** | 1/1 |
| **with-skill** | sonnet | nest | npm | 4 | **90** | 4/4 |
| **with-skill** | sonnet | next | bun | 2 | **95** | 2/2 |
| **with-skill** | sonnet | next | npm | 6 | **100** | 6/6 |
| **with-skill** | sonnet | next | pnpm | 3 | **98** | 3/3 |
| **with-skill** | sonnet | next | yarn | 1 | **98** | 1/1 |
| **with-skill** | sonnet | rust | cargo | 4 | **91** | 4/4 |
| **with-skill** | sonnet | springboot | gradle | 3 | **89** | 3/3 |

Reproduce this table exactly with `node summarize.mjs` against the committed `results/scores.tsv` —
no agent runs required.

## Honest denominator — read this before trusting the table

- **Env-failure exclusion asymmetry.** `with-skill` runs were included (scored) at only **≈44%**
  (34/77 attempts); `bare` runs at **≈91%** (31/34 attempts). Session limits and mid-response API
  drops hit the longer `with-skill` runs (a full scaffold + every gate wired) far harder than the
  short bare runs. Excluded attempts are **never scored** either direction — they are not imputed as
  wins or losses, and they are not silently dropped from the record: every attempt's disposition
  (`scored`, `env_failure`, `evaluate_error`, `scored_prefix`, `scored_prescorerfix`,
  `scored_predetectfix`, `scored_invalid_*`) is a row in `results/scores.tsv`. `summarize.mjs`
  prints the exclusion breakdown by status on every run (23 `env_failure`, 7 `evaluate_error`, 3
  `scored_prefix` from an early harness bug archived rather than rewritten, etc.) — read it, don't
  take the headline medians on faith.
- **Thin cells.** `go` is n1 (one successful attempt out of 7 tries — six died to env failures before
  one completed); `bun` is n2; `yarn` is n1 (a smoke test, not a distribution). Treat single-digit-n
  cells as directional, not statistically settled — they are reported because a benchmark that hides
  its weak cells isn't trusted, not because n=1 supports a strong claim.
- **`audit`/`e2e` are scored as config-present, not executed**, in every row of the table above.
  `evaluate.sh` without `--e2e` (the default, and how every scored run here was produced) substitutes
  a deterministic "is the gate wired and does its config/spec exist" check
  (`lib/config-present.mjs`) for actually running the gate — network/browser availability makes real
  e2e execution unsuitable for the default benchmark loop. This is a real limitation, not a rounding
  error: a repo can score full marks on `audit`/`e2e` with a gate that is wired but would fail if run.
  `--e2e` exists for anyone who wants to pay that cost.
- **Single machine.** Every row was produced on one pre-provisioned macOS arm64 box with the full
  toolchain (Node, `uv`, Go, Rust/cargo, JDK 21, `golangci-lint`, `lefthook`, etc.) already on PATH.
  No cross-platform (Linux/Windows) or cross-architecture (x86_64) data exists yet.

## Instrument-fix history (read before assuming a low score is a skill gap)

Four defects were found and fixed in the **harness/scorer**, not the skill, across the runs behind
this table (D20, D20-amendment, D21, D22 in the source workspace's `CRAFT-DECISIONS.md`): nested-git
detection silently skipping `git init`, a skill-placement bug that made the with-skill arm run
effectively bare, two teeth-probe blind spots (an ignorable lint identifier, a coverage mutation too
small to trip a real floor), and a scorer/agent toolchain mismatch that capped every Rust/Spring Boot
`with-skill` row at a uniform 50 with all gates red even though the repos were canon-correct. The
pattern that caught every one of them: **uniform anomalies — exact-50 caps, 81-byte transcripts,
one model failing every run — are instrument smells, not model smells.** Investigate the referee
(the scorer, the harness, the toolchain env) before concluding the player (the skill) failed. Anyone
extending this benchmark to a new stack or PM should apply the same discipline before trusting a
surprising cell.

## Honest negatives

- **Bare scores are weak even for a strong model.** Bare Opus medians **39.5** — barely above bare
  Haiku's **26** — on the same `next`/`npm` task. Model strength alone does not produce a wired,
  toothed guardrail canon; the gap the skill closes is a methodology gap, not a capability gap the
  frontier model was already closing on its own.
- **The skill flattens the model curve.** With the skill, Haiku (94), Sonnet (90-100 across stacks),
  and Opus (94) all land in the same **94-100** band on `next`/`npm` — the skill's value is largest
  for the weakest model in absolute terms, but it moves every tier into the same narrow, high band
  rather than only lifting the strong model further.
- **`T2`-style environmental-red behavior is benchmark-unmeasured.** Because env-failed attempts are
  excluded by policy rather than scored, this benchmark says nothing about how gracefully either arm
  degrades under a genuine environment failure (missing tool, no network) — only about outcomes when
  the run completed.
- **The scorer's `PREFERRED_NAME_HINTS`** (used to disambiguate which subdirectory is the scaffolded
  project root) couple loosely to the adapter's seed app names. A stack whose adapter changes its
  default scaffold name, or a new stack added to `task/stacks.json`, should double-check
  `lib/detect-gates.mjs` picks the right directory before trusting its scored rows.

## Reproduce

**Offline (no API key, no agent runs) — verify the scorer itself:**

```bash
cd benchmarks/app-ai-guardrails
fixtures/make-golden.sh next && fixtures/make-bare.sh next   # builds .gen/next-{golden,bare}
                              # (gitignored; regenerate any time — needs network for the scaffolder)
node test-checker.mjs        # self-tests the deterministic probes against those two fixtures
node summarize.mjs           # reproduce the results table above from the committed results/scores.tsv
```

**Maintainer-run (produces new rows; needs the `claude` CLI + credentials):**

```bash
cd benchmarks/app-ai-guardrails
claude setup-token                                  # mint a revocable headless credential once
export CLAUDE_CODE_OAUTH_TOKEN=...                  # or export ANTHROPIC_API_KEY instead
./drive-cells.sh my-lane with-skill:sonnet:next:npm:3 bare:sonnet:next:npm:3
node summarize.mjs
```

One lane (one sequential `drive-cells.sh` invocation) at a time per credential — see "Containment"
above. `run-arm.sh` reads the skill from `../../skills/app-ai-guardrails/SKILL.md`, so the benchmark
always tests what `npx skills add` actually installs.

Prereqs: Node 18+, the `claude` CLI, a headless credential, and — per stack —
the toolchain the skill itself requires (see `skills/app-ai-guardrails/README.md` Prerequisites):
`uv` for Django, Go + `golangci-lint` + `just` for Go, `cargo` (+`cargo-llvm-cov`/`cargo-deny`) for
Rust, JDK 21 for Spring Boot.
