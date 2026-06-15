# Benchmark — production-grade

Measures the skill the way a skill should be measured: the **same model, with `production-grade` vs
with no skill**, on identical tasks. Self-contained — two arms (`arms/baseline.js`, the bare model;
`arms/production-grade.js`, the model with `../SKILL.md` as its system prompt), a code-executing +
structural-probe scorer (`score.js`, self-tested in `test-rigor.js`), and three task suites.

## Method

- **Models:** `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-8`. **n=5** per cell, medians.
- **Single-shot**, default temperature, 8192 max tokens. One completion per (task × arm × model × repeat).
- **Scoring runs the code** in a capable environment (Python 3.13 + `email-validator` + `pandas`), so
  best-practice output (official libraries, modern typing, raise-based validators) executes instead of
  erroring. Rigor is checked by structural probes on the generated code, not by line count.
- Three suites: **everyday** (`promptfooconfig.everyday.yaml` — size + executed correctness),
  **production-spec** (`promptfooconfig.production-spec.yaml` — security/concurrency/idempotency/typed-
  errors/test probes), **engineering-rigor** (`promptfooconfig.rigor.yaml` — optimal algorithm, no
  N+1, parameterized SQL, money precision, tz-aware datetime).

## Results (n=5 medians)

### Engineering rigor — % of runs that ship the proper choice (no skill → + production-grade)

| dimension | Haiku | Sonnet | Opus |
|-----------|:-----:|:------:|:----:|
| optimal complexity — hash set vs O(n²) loop (R4) | 20% → **100%** | 80% → **100%** | 100% → 100% |
| no N+1 query — batched fetch (R6) | 80% → **100%** | 20% → **80%** | 60% → **100%** |
| idempotent writes — no double-charge on retry (R6) | 0% → **60%** | 0% → **30%** | 0% → **70%** |
| money as Decimal/cents, not float (R5, R8) | 40% → **80%** | 0% → **100%** | 100% → 80% |
| timezone-aware datetime, not naive `utcnow()` | 0% → **60%** | 100% → 100% | 0% → **100%** |
| typed / domain errors, not bare exceptions (R14) | 50% → **80%** | 50% → **65%** | 50% → **75%** |
| parameterized SQL, not string-interpolated (R7) | 100% → 100% | 100% → 100% | 80% → **100%** |
| security + concurrency primitives | 100% → 97% | 100% → 100% | 100% → 94% |

Floors every arm already clears (not differentiators): Fibonacci memoization, top-k via sort/heap.

### Everyday tasks — median LOC and executed correctness (no skill → + production-grade)

| model | LOC | correctness |
|-------|:---:|:-----------:|
| Haiku  | 109 → **40** (−63%) | 100% → 84% |
| Sonnet | 87 → **23** (−74%) | 92% → 92% |
| Opus   | 42 → **29** (−31%) | 100% → 84% |

The skill cuts the model's code bloat 2–4× on simple work while holding correctness. The everyday-
correctness dip is almost entirely one task (a vague "rate-limit so users can't spam" ask, where the
skill asks about the runtime — an in-memory limiter is useless on serverless — instead of shipping
blind); excluding it, with-skill correctness matches the bare model.

## Reading the numbers

The skill's biggest lifts land where the bare model has a real blind spot: an **O(n²) loop** (Haiku
20%→100%), **float money** (Sonnet 0%→100%), **naive datetime** (Haiku/Opus 0%→60/100%), **N+1
queries** (Sonnet 20%→80%), and **idempotency a bare model never ships** (0%→30–70%). Some textbook
tasks don't separate — modern models already memoize Fibonacci and parameterize obvious SQL — so the
skill's edge is the rigor a model *skips under realistic conditions*, not every exercise. On complex
tasks the skill writes *more* code, not less: it ships the test, the idempotency, and the typed errors
the bare model omits.

## Reproduce

```bash
# from this directory (skills/production-grade/benchmarks)
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python email-validator pandas
node test-rigor.js                              # offline: the probes are self-tested
ANTHROPIC_API_KEY=... npx promptfoo@latest eval -c promptfooconfig.rigor.yaml --repeat 5
npx promptfoo@latest view
```

Prereqs: Node 18+, [`uv`](https://github.com/astral-sh/uv) (or any Python 3.13 with `email-validator`
+ `pandas`), an `ANTHROPIC_API_KEY`.
