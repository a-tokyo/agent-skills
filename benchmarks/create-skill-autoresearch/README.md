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
  **No LLM judge anywhere** — the answer key is the ground truth.
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

_Pending: runs are executed with `arms/run-arm.sh` (below); results land in `results/runs.csv`
and per-run `score-report.json`, and the medians are published here._

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
