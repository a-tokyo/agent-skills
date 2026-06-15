# production-grade

A principle-engineering posture as an agent skill. Add it to any coding model and it engineers like a
senior: it plans before it codes, reaches for the proper algorithm and data structure, makes writes
idempotent, types its errors, validates at boundaries, parameterizes its queries, and keeps money and
time correct — the judgment an unguided model skips. The skill is [`SKILL.md`](SKILL.md); this page is
the benchmark.

## Benchmark — same model, with the skill vs without

A skill's job is to improve the model running it, so the benchmark measures exactly that: the **same
model, with `production-grade` vs with no skill**, on identical tasks. Haiku 4.5, Sonnet 4.6, Opus 4.8;
n=5, medians. Code runs in a capable environment (Python 3.13 + the libraries best-practice code
reaches for); rigor is checked by structural probes on the generated code. Method, scorers, and raw
data live in
[`agent-skills-workspace`](https://github.com/a-tokyo/agent-skills-workspace/tree/main/production-grade/benchmarks).

### The rigor an unguided model skips

Share of runs that ship the correct engineering choice — same model, without skill → with
`production-grade` (range across Haiku/Sonnet/Opus):

| engineering choice | without skill | + production-grade |
|--------------------|--------------:|-------------------:|
| **optimal algorithmic complexity** — hash set, not an O(n²) loop (R4) | as low as **20%** | **~100%** |
| **no N+1 query** — one batched fetch, not a query per row (R6) | 20–80% | 80–100% |
| **idempotent writes** — no double-charge on retry (R6) | **0%** | 30–70% |
| **money as integer/Decimal**, never binary float (R5, R8) | 0–100% | 80–100% |
| **timezone-aware datetime**, not naive `utcnow()` | often **0%** | 60–100% |
| **typed / domain errors**, not bare exceptions (R14) | 50% | 65–80% |
| **parameterized SQL**, not string-interpolated (R7) | 80–100% | 100% |

An unguided model **never** makes a money transfer idempotent, routinely writes an O(n²) loop where a
hash set is O(n), stores money in floats, and compares naive timestamps. `production-grade` is the
review layer that catches each one before it ships.

### Correctness holds, and it stops over-building

With a harness that runs the code (rather than rewarding the shortest snippet), correctness is on par
with the unguided model on everyday tasks — and the skill cuts the model's bloat **2–4×** on simple
work (e.g. a Haiku everyday task drops from 109 to 40 median lines) while keeping it correct.

## Honest caveats

- **Some tasks don't separate.** Modern models already memoize Fibonacci, reach for a heap on top-k,
  and parameterize obvious SQL. The skill's edge is the rigor a model *skips under realistic
  conditions*, not every textbook exercise.
- **Calibrated for non-trivial work.** On a vague, security-adjacent ask ("rate-limit so users can't
  spam") it asks about the runtime — an in-memory limiter is useless on serverless — rather than
  shipping blind. That reads as a miss on a single-shot benchmark; in a real session it is the right
  question.
- **On complex tasks it writes more, not less** — it ships the test, the idempotency, the typed errors
  the bare model omits. The trade is correctness for lines, by design.

## Versus minimalist skills (optional)

Minimal-by-default skills write less code, but on the same production-spec tasks one popular minimalist
skill shipped **0% idempotent writes** and **0% typed errors** and failed critical security/concurrency
probes ~27% of the time on Haiku and Opus. Fewer lines, less rigor.

## Install

```bash
npx skills add a-tokyo/agent-skills --skill production-grade
```

## Reproduce

```bash
cd benchmarks            # in agent-skills-workspace/production-grade
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python email-validator pandas
ANTHROPIC_API_KEY=... npx promptfoo@latest eval -c promptfooconfig.tier3.yaml --repeat 5
node analyze.v2.js results/<output>.json tier3
```
