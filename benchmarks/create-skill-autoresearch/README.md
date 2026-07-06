# Benchmark — create-skill-autoresearch

End-to-end factory benchmark: the **same model builds the same skill from the same brief**,
bare vs. with the official single-pass `skill-creator` vs. with the full
`create-skill-autoresearch` 5-phase pipeline — and the *produced skill* is scored by
executing it, not by reading it.

## Task

Build a `conventional-commits` skill from [`task/brief.md`](task/brief.md): turn a staged diff
summary into a commit message following a team's Conventional Commits conventions. The six gold
standards in `task/input/` embed **team-specific conventions beyond the public spec** (scope =
top-level directory, `build(deps)` for dependency bumps, dual breaking-change markers `!` +
`BREAKING CHANGE:` footer, `- ` bullet bodies for multi-file changes, `revert:` + SHA body
format). A generic spec-restating skill misses them; a builder that actually studies the gold
standards captures them. That gap is what the benchmark measures.

Three holdout cases (`task/holdout/`) recombine those conventions and are **never shown to any
arm** — they exist only for the scorer.

## Arms

| Arm | What the builder gets |
|-----|----------------------|
| `bare` | brief + gold standards + spec, no skills |
| `skill-creator` | same + Anthropic's official `skill-creator` (vendored at `../../.agents/skills/skill-creator`) |
| `factory` | same + the live `../../skills/create-skill-autoresearch` + its companions (`companions/`) — full 5-phase pipeline with a 6-iteration autoresearch budget |

Same builder model per cell; the factory arm always tests the shipped skill files, so the
benchmark measures what users install.

## Scoring — deterministic, arm-independent, fixed before any run

`scoring/score.mjs <produced-skill-dir>` emits METRIC lines:

- **execution (weight 0.60)** — a fixed executor (Haiku, temperature 0) receives the produced
  SKILL.md as its system prompt and each holdout diff summary as the user message; the emitted
  commit message is parsed field-by-field against `task/holdout/answer-key.json` (format, type,
  scope, breaking markers, subject style, bullet body, revert SHA reference, keywords).
  **No LLM judge anywhere** — the answer key is the ground truth. With an `ANTHROPIC_API_KEY`
  the executor is a direct API call; with only a `CLAUDE_CODE_OAUTH_TOKEN` it falls back to
  `claude -p --system-prompt` under a scratch HOME (temperature not pinnable there; the checks
  are coarse enough that this does not flip results — the report records which executor ran).
- **compliance (0.25)** — platform + brief constraints on the skill files: frontmatter, name
  rules, description limits, `disable-model-invocation: true` (the brief demands a user-invoked
  skill), body < 150 lines, references one level deep.
- **craft (0.15)** — user-invoked description craft (one-line human summary, no trigger-list
  scaffolding) and at least one concrete example in the body.

The scorer's probes are themselves unit-tested offline: `node scoring/selftest.mjs` must print
`SELFTEST PASS` before any score is trusted (perfect answers score 1.0, generic answers ≤ 0.5,
spurious breaking markers are penalized, fixtures discriminate good vs bad skills).

Ambiguity is handled in the answer key, not the scorer: where two commit types are defensible
(holdout h1), the key accepts the set.

## Results

Runs of 2026-07-06, builder models `claude-sonnet-5` and `claude-haiku-4-5-20251001`; per-run
scores in [`results/scores.csv`](results/scores.csv). Overall = 0.60·execution + 0.25·compliance
+ 0.15·craft. Medians per cell (per-run values in parentheses):

| Arm | Sonnet overall | Haiku overall | Sonnet execution | Haiku execution |
|-----|---------------|---------------|------------------|-----------------|
| bare | 0.896 (.896/.896) | **0.928** (.946/.909) | 0.911 | 0.932 |
| skill-creator | 0.896 (.871/.921) | 0.846 (.821/.871) | 0.911 | 0.869 |
| factory | 0.896 (.921/.871)¹ | 0.896 (n=1) | 0.911 | 0.911 |

¹ Sonnet factory cell: the two disclosed-protocol runs (see notes below). A third, earlier run
(0.871) paused after Phase 3 before the prompt was hardened and is recorded in `scores.csv` but
not counted as a full-pipeline run.

### Honest reading — no overall uplift on this task

- **The factory does not beat the bare model here.** This task sits at the models' floor: given
  6 gold examples, both Sonnet and Haiku one-shot a near-ceiling skill (bare-Haiku hit 0.946).
  Per this repo's benchmarking standard, cells both arms pass are floors, not differentiators —
  and this first task turned out to be one. A discriminating task needs a convention surface too
  large to one-shot (dozens of interacting rules, or a procedural multi-step skill).
- **The factory beats the official single-pass `skill-creator` on Haiku** (0.896 vs 0.846
  median), and ties everything on Sonnet.
- **Craft variance dominates overall spreads.** The user-invoked description rules (one-line,
  no trigger scaffolding) were the biggest mover: factory-sonnet-2 was the only Sonnet run to
  score craft 1.000; factory-sonnet-3 wrote a long trigger-style description (craft 0.333) —
  run-to-run description style is not yet predictable for any arm.
- **Process fidelity is where the factory visibly differs.** Only factory runs produced
  verifiable pipeline artifacts: an internal rubric carrying the v0.1.0 `predictability`
  dimension, an executed craft pass (leading-word/no-op prune) in the autoresearch log, and a
  Phase-5 panel whose Devil's Advocate caught and fixed a real bug the improvement loop had
  introduced (factory-sonnet-3, internal panel score 0.66 → 0.84 after fixes).
- **Nobody passed the h3 no-bullets check** (a single-file revert should be subject +
  SHA-sentence only): every arm's skill emitted explanation bullets. The check is achievable
  but was missed universally; it stays in the key.

### Protocol notes (disclosed)

- Two Sonnet factory runs ended early by pausing to address a user that doesn't exist in
  `claude -p`: one before the arm prompt was hardened (kept out of the cell), one from a stdin
  hiccup, fixed by `< /dev/null` in the runner (kept in the cell as a phases-1-4 run at 0.921 —
  its truncation cost iterations, not phases 1-3 quality). Both fixes are committed in
  `arms/run-arm.sh`; rerunning the cell with the fixed runner is the first follow-up.
- Cells are n=2 (n=1 for factory-Haiku) — below the n=5 standard; medians here bound the story
  but not tightly. The executor ran through the `claude` CLI fallback (subscription auth; see
  scoring note above).

## Reproduce

```bash
cd benchmarks/create-skill-autoresearch
node scoring/selftest.mjs                      # offline: prove the probes discriminate

# headless auth for the contained runs: mint once with `claude setup-token`
export CLAUDE_CODE_OAUTH_TOKEN=...             # or ANTHROPIC_API_KEY=...

arms/run-arm.sh bare sonnet bare-sonnet-1
arms/run-arm.sh skill-creator sonnet sc-sonnet-1
arms/run-arm.sh factory sonnet factory-sonnet-1

# score a finished run (needs ANTHROPIC_API_KEY for the Haiku executor)
node scoring/score.mjs runs/bare-sonnet-1/work/output/conventional-commits \
  --executor-out runs/bare-sonnet-1
```

Each run executes in an **isolated HOME** under `/private/tmp/create-skill-autoresearch-bench/`
(fake `$HOME`, own git config, XDG dirs) so a `--dangerously-skip-permissions` builder can never
touch the maintainer's real config or credentials; skills are injected per-arm as user-level
skills inside that fake HOME. n ≥ 2 runs per cell; report medians and per-run spread, including
cells where arms tie (honest negatives).

Prereqs: Node 18+, the `claude` CLI, and a headless credential as above.
