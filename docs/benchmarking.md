# Benchmarking

Every skill ships with a benchmark proving it earns its place. This is the standard; the worked
example and reproduce steps live in [`../benchmarks/README.md`](../benchmarks/README.md) and each
[`../benchmarks/<skill>/README.md`](../benchmarks/production-grade/README.md).

## The standard

- **Measure same-model uplift.** Run the *same model* twice on identical tasks — once with the skill
  as its system prompt, once bare — and report the delta. A skill's value is what it changes, not how
  the model scores in absolute terms.
- **Two arms.** `arms/baseline.js` (bare model) and `arms/<skill>.js` (model + the live
  `../../skills/<skill>/SKILL.md`). The skill arm reads the shipped file, so the benchmark always
  tests what users install.
- **Score by executing, not by eyeballing.** The scorer runs the generated code in a capable
  environment and checks behaviour with structural probes, not line counts or string matches. The
  scorer is itself unit-tested (e.g. `test-rigor.js`) so a green run means the probes work.
- **n=5 per cell, medians.** Single-shot, default temperature. Report per model — Haiku, Sonnet, Opus
  — since uplift differs sharply by model.
- **Honest negatives.** List the cells where the skill *doesn't* separate (tasks both arms already
  pass are floors, not differentiators) and never publish a cell where the skill moves a column
  backwards. A benchmark that only shows wins isn't trusted.

## Layout

Benchmarks live in `benchmarks/<skill>/`, **outside `skills/`**, so they don't ship on
`npx skills add`. Each is self-contained — arms, a scorer, suite configs, and a `README.md` with
**method · per-model results · reproduce steps**. Run them from inside the repo so the arms resolve
`../../skills/<skill>/SKILL.md`.

## Reproduce (example: production-grade)

```bash
cd benchmarks/production-grade
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python email-validator pandas pytest
node test-rigor.js                                          # offline: self-test the probes
ANTHROPIC_API_KEY=... npx promptfoo@latest eval -c promptfooconfig.rigor.yaml --repeat 5
npx promptfoo@latest view
```

Prereqs: Node 18+, [`uv`](https://github.com/astral-sh/uv) (or any Python 3.13 with `email-validator`,
`pandas`, `pytest`), and an `ANTHROPIC_API_KEY`.
