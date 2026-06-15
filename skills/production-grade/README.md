# production-grade

A principle-engineering posture as an agent skill. It reads the local codebase first, matches its
idiom, and ships changes that earn every character — substrate-agnostic. The skill itself is
[`SKILL.md`](SKILL.md); this page is the benchmark.

## What it is for

Add it to any coding model. On non-trivial work it makes the model plan before it codes, classify the
problem, reach for the right data structure and the official library, make writes idempotent, type its
errors, validate at boundaries, and test the critical path — the things a senior engineer does and an
unguided model skips. On simple work it makes the model stop over-building.

## Benchmark — same model, with the skill vs without

A skill's job is to improve the model running it, so the benchmark measures exactly that: the **same
model, with `production-grade` vs with no skill**, on identical tasks. Haiku 4.5, Sonnet 4.6, Opus 4.8;
`--repeat 5`, medians. Code runs in a capable environment (Python 3.13 + the libraries best-practice
code reaches for); rigor is checked by structural probes (security, concurrency, idempotency, typed
errors, a shipped test). Harness and raw data:
[`agent-skills-workspace`](https://github.com/a-tokyo/agent-skills-workspace/tree/main/production-grade/benchmarks).

### It cuts bloat on simple tasks

Median lines of code on five everyday tasks (email validator, debounce, CSV sum, countdown, rate
limiter), correctness held:

| model | no skill | + production-grade | change |
|-------|---------:|-------------------:|-------:|
| Haiku  | 109 | **40** | −63% |
| Sonnet | 87  | **23** | −74% |
| Opus   | 42  | **29** | −31% |

### It adds the rigor an unguided model omits

On production-spec tasks (signup/login, a money-transfer ledger, request validation), share of runs
that ship the property — same model, without → with the skill:

| property | Haiku | Sonnet | Opus |
|----------|------:|------:|-----:|
| **idempotent writes** (no double-spend on retry) | 0% → **60%** | 0% → **30%** | 0% → **70%** |
| **typed / domain errors** (not bare exceptions) | 50% → **80%** | 50% → **65%** | 50% → **75%** |
| security + concurrency primitives | 100% → 97% | 100% → 100% | 100% → 94% |

The unguided model **never** makes a money transfer idempotent (0% on every model); with the skill it
does 30–70% of the time. Security and concurrency primitives the unguided model already gets are held,
not lost.

### Honest caveats

- **It is calibrated for non-trivial work.** On a vague, security-adjacent ask ("add rate limiting so
  users can't spam it") the skill often asks about the runtime — an in-memory limiter is useless on
  serverless — instead of shipping blind. That counts against it on a single-shot benchmark (it ships
  no code ~half the time on that task) and accounts for nearly all of the everyday-correctness gap;
  excluding it, with-skill correctness matches the bare model.
- **On complex tasks it writes more, not less.** It ships the test, the security primitives, the typed
  errors, and the idempotency the bare model skips — so its production-spec output is larger. That is
  the trade: more correct, more complete, not fewer lines.

### Versus minimalist skills (optional)

Minimal-by-default skills write less code than `production-grade`, but on the same production-spec
tasks one popular minimalist skill shipped **0% idempotent writes** and **0% typed errors**, and failed
critical security/concurrency probes ~27% of the time on Haiku and Opus. Fewer lines, less rigor.
`production-grade` is heavier by design and trades lines for correctness.

## Install

```bash
npx skills add a-tokyo/agent-skills --skill production-grade
```

## Reproduce

```bash
cd benchmarks            # in agent-skills-workspace/production-grade
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python email-validator pandas
ANTHROPIC_API_KEY=... npx promptfoo@latest eval -c promptfooconfig.yaml --repeat 5
node analyze.v2.js results/<output>.json tier1
```

Method, scorers, and per-model raw numbers live in
[`benchmarks/`](https://github.com/a-tokyo/agent-skills-workspace/tree/main/production-grade/benchmarks)
and `benchmarks/results/`.
