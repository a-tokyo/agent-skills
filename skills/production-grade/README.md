# production-grade

A principle-engineering posture as an agent skill. Add it to any coding model and it engineers like a
senior: it plans before it codes, reaches for the proper algorithm and data structure, makes writes
idempotent, types its errors, validates at boundaries, parameterizes its queries, and keeps money and
time correct — the judgment an unguided model skips. The skill is [`SKILL.md`](SKILL.md).

## Benchmark — same model, with the skill vs without

A skill's job is to improve the model running it, so the benchmark measures exactly that: the **same
model, with `production-grade` vs with no skill**, on identical tasks. Two arms (the bare model; the
model with `SKILL.md` as its system prompt), three models, **n=5** per cell, medians. Scoring **runs
the generated code** in a capable environment (Python 3.13 + the libraries best-practice code reaches
for) and checks engineering rigor with structural probes — it does not reward the shortest snippet.
Full method, scorer, and per-suite configs are in [`benchmarks/`](benchmarks/) (self-tested,
reproducible).

### The rigor an unguided model skips — per model

Share of runs that ship the correct engineering choice, **no skill → + production-grade**:

| engineering choice (rule) | Haiku | Sonnet | Opus |
|---------------------------|:-----:|:------:|:----:|
| optimal complexity — hash set vs O(n²) loop (R4) | **20% → 100%** | 80% → 100% | 100% → 100% |
| no N+1 query — batched fetch (R6) | 80% → 100% | **20% → 80%** | 60% → 100% |
| idempotent writes — no double-charge on retry (R6) | **0% → 60%** | **0% → 30%** | **0% → 70%** |
| money as Decimal/cents, not float (R5, R8) | 40% → 80% | **0% → 100%** | 100% → 80% |
| timezone-aware datetime, not naive `utcnow()` | **0% → 60%** | 100% → 100% | **0% → 100%** |
| typed / domain errors, not bare exceptions (R14) | 50% → 80% | 50% → 65% | 50% → 75% |
| parameterized SQL, not string-interpolated (R7) | 100% → 100% | 100% → 100% | 80% → 100% |
| security + concurrency primitives (held) | 100% → 97% | 100% → 100% | 100% → 94% |

The bare model **never** makes a money transfer idempotent (0% on every model), and on weaker models it
routinely writes an O(n²) loop, stores money in floats, and compares naive timestamps. The skill is the
review layer that catches each one.

### Everyday tasks — it also stops over-building

Median lines of code on five everyday tasks, correctness held (**no skill → + production-grade**):

| model | median LOC | executed correctness |
|-------|:----------:|:--------------------:|
| Haiku  | 109 → **40** (−63%) | 100% → 84% |
| Sonnet | 87 → **23** (−74%) | 92% → 92% |
| Opus   | 42 → **29** (−31%) | 100% → 84% |

## Honest caveats

- **Some tasks don't separate.** Modern models already memoize Fibonacci, reach for a heap on top-k,
  and parameterize obvious SQL. The skill's edge is the rigor a model *skips under realistic
  conditions*, not every textbook exercise.
- **The everyday-correctness dip is one task.** On a vague, security-adjacent ask ("rate-limit so
  users can't spam") the skill asks about the runtime — an in-memory limiter is useless on serverless —
  rather than shipping blind; that reads as a miss on a single-shot benchmark. Excluding it, with-skill
  correctness matches the bare model.
- **On complex tasks it writes more, not less** — it ships the test, the idempotency, and the typed
  errors the bare model omits. The trade is correctness for lines, by design.

## Install

```bash
npx skills add a-tokyo/agent-skills --skill production-grade
```

Reproduce the numbers above with [`benchmarks/`](benchmarks/).
