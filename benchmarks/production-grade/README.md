# Benchmark — production-grade

Measures the skill the way a skill should be measured: the **same model, with `production-grade` vs
with no skill**, on identical tasks. Self-contained — two arms (`arms/baseline.js`, the bare model;
`arms/production-grade.js`, the model with `skills/production-grade/SKILL.md` as its system prompt), a code-executing +
structural-probe scorer (`score.js`, self-tested in `test-rigor.js`), and three task suites.

## Method

- **Models:** `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-8`. **n=5** per cell, medians.
- **Single-shot**, default temperature, 8192 max tokens. One completion per (task × arm × model × repeat).
- **Scoring runs the code** in a capable environment (Python 3.13 + `email-validator` + `pandas`), so
  best-practice output (official libraries, modern typing, raise-based validators) executes instead of
  erroring. Rigor is checked by structural probes on the generated code, not by line count.
- Three suites: **everyday** (`promptfooconfig.everyday.yaml` — size + executed correctness),
  **production-spec** (`promptfooconfig.production-spec.yaml` — security/concurrency/idempotency/test
  probes), **engineering-rigor** (`promptfooconfig.rigor.yaml` — optimal algorithm, no N+1,
  parameterized SQL, money precision, tz-aware datetime).

## Results (n=5 medians)

### Engineering rigor — % of runs that ship the proper choice (no skill → + production-grade)

| dimension | Haiku | Sonnet | Opus |
|-----------|:-----:|:------:|:----:|
| idempotent writes — no double-charge on retry (R6, n=10) | 0% → **90%** | 0% → **80%** | 0% → **70%** |
| money as Decimal, not float (R5, R8) | 40% → **80%** | 0% → **100%** | 100% → 80% |
| timezone-aware datetime, not naive `utcnow()` | 0% → **60%** | 100% → 100% | 0% → **100%** |
| optimal complexity — O(n) hash set vs O(n²) loop (R4) | 20% → **100%** | 80% → **100%** | 100% → 100% |
| no N+1 query — batched fetch (R6) | 80% → **100%** | 20% → **80%** | 60% → **100%** |

The idempotency row combines the two captured production-spec runs (n=10 per cell); other rows are n=5.
One cell moves backwards at this sample size (Opus money 100%→80%, one run in five) — printed, not
hidden; at n=5 a near-ceiling cell can swing on a single run. Tasks both arms already handle (Fibonacci
memoization, top-k, parameterized SQL, password-hashing and money-locking primitives) sit at ~100% with
or without the skill, so they're floors, not differentiators, and aren't listed.

### Everyday tasks — median LOC and executed correctness (no skill → + production-grade)

| model | LOC (5 tasks) | correctness (4 self-contained tasks) |
|-------|:---:|:-----------:|
| Haiku  | 109 → **40** (−63%) | 100% → **100%** |
| Sonnet | 87 → **23** (−74%) | 90% → **100%** |
| Opus   | 42 → **29** (−31%) | 100% → 100% |

On the fifth task (a vague "rate-limit so users can't spam" ask) the skill often asks about the runtime
— an in-memory limiter is useless on serverless — instead of shipping blind: it ships no code in 4/5
Haiku, 3/5 Opus, and 2/5 Sonnet runs, which the correctness scorer counts as a miss. That is the senior
question, not a wrong answer — but counted against it, five-task correctness is 84–92% with the skill vs
92–100% without. The skill cuts code 1.4–4× while holding correctness.

## Reading the numbers

The skill's biggest lifts land where the bare model has a real blind spot: an **O(n²) loop** (Haiku
20%→100%), **float money** (Sonnet 0%→100%), **naive datetime** (Haiku/Opus 0%→60/100%), **N+1
queries** (Sonnet 20%→80%), and **idempotency a bare model never ships** (0%→70–90%). Some textbook
tasks don't separate — modern models already memoize Fibonacci and parameterize obvious SQL — so the
skill's edge is the rigor a model *skips under realistic conditions*, not every exercise. On complex
tasks the skill writes *more* code, not less: it ships the test, the idempotency, and the error
handling the bare model omits.

## Reproduce

```bash
# from this directory (benchmarks/production-grade)
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python email-validator pandas pytest
node test-rigor.js                              # offline: the probes are self-tested
# one suite per table: rigor → the engineering-rigor table; everyday → LOC/correctness;
# production-spec → the idempotency row (run twice for n=10)
ANTHROPIC_API_KEY=... npx promptfoo@latest eval -c promptfooconfig.rigor.yaml --repeat 5
ANTHROPIC_API_KEY=... npx promptfoo@latest eval -c promptfooconfig.everyday.yaml --repeat 5
ANTHROPIC_API_KEY=... npx promptfoo@latest eval -c promptfooconfig.production-spec.yaml --repeat 5
npx promptfoo@latest view
```

Prereqs: Node 18+, [`uv`](https://github.com/astral-sh/uv) (or any Python 3.13 with `email-validator`,
`pandas`, `pytest`), an `ANTHROPIC_API_KEY`. The `production-grade` arm reads
`../../skills/production-grade/SKILL.md`, so run it from inside the repo.
