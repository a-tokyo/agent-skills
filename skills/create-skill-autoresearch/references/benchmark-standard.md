# Benchmark Standard

How to prove a finished skill earns its place. The autoresearch loop in Phase 4 optimizes *absolute*
output quality against gold standards; this is the separate question of whether the skill **changes
what the model does**. Answer it before shipping.

## Contents
- [The gate: same-model uplift](#the-gate-same-model-uplift)
- [Arms](#arms)
- [Score by executing, not by reading](#score-by-executing-not-by-reading)
- [Unit-test the scorer](#unit-test-the-scorer)
- [Sample size, and why per-model](#sample-size-and-why-per-model)
- [Honest negatives](#honest-negatives)
- [Retry parity](#retry-parity)
- [Burned fixtures](#burned-fixtures)
- [Procedural skills: execute against a real artifact](#procedural-skills-execute-against-a-real-artifact)
- [Where benchmarks live](#where-benchmarks-live)
- [Pre-publication checklist](#pre-publication-checklist)

## The gate: same-model uplift

Run the **same model** twice on identical tasks — once with the skill as its system prompt, once bare —
and report the delta. A skill's value is what it changes, not the absolute score.

This matters because absolute scores are dominated by the model, not the skill. A capable model scores
well on an easy task with or without help; a weak model scores badly either way. Only the delta
isolates the skill's contribution.

Do not reuse the Phase 4 rubric score as the answer here. That number measures output quality against
gold standards and has no bare-model comparison in it. Reading effectiveness off it is the most common
way to conclude that a working skill is useless.

## Arms

| Arm | What the model gets |
|-----|--------------------|
| `bare` | the task, no skill |
| `skill` | the task + the **shipped** skill file as system prompt |
| `skill+panel` | the task + the skill, run under a retrying verifier panel — the shape real usage takes |

Two arms are the minimum; the third is what makes the number resemble production. See
[Retry parity](#retry-parity).

The skill arm must read the **shipped** file — `skills/<name>/SKILL.md`, not a draft copy — so the
benchmark always tests what a user installs. A benchmark that scores a working copy will drift away
from reality silently.

Hold everything else fixed: same model, same temperature, same task text, same scorer. If the arms
differ in more than one respect, the delta is not attributable.

## Score by executing, not by reading

Prefer a **deterministic answer key** over an LLM judge. Run the produced artifact and check its
behaviour: parse the output field by field, run the generated code, diff against a captured baseline.

An answer key is cheap, repeatable, and cannot flatter you. Reserve LLM-as-judge for dimensions that
genuinely require judgment — prose clarity, curation quality — and mix the two: deterministic checks
emit `METRIC` lines directly, judges handle the rest.

Handle ambiguity **in the key, not in the scorer**: where two answers are defensible, have the key
accept the set. A scorer that "understands" ambiguity is a scorer nobody can audit.

## Unit-test the scorer

The scorer is code, and an unverified scorer invalidates every number it produces. Ship a self-test
that runs offline and must pass before any score is trusted:

- a perfect reference answer scores 1.0
- a deliberately generic answer scores low
- a wrong-but-plausible answer is penalized for the right reason
- fixtures that should discriminate actually do

Run it first, every time. `SELFTEST PASS` before a headline number, or the number is unsupported.

## Sample size, and why per-model

**n=5 per cell, report medians**, single-shot at default temperature. **Always disclose the actual n.**
A thin cell is publishable; a thin cell presented as settled is not.

Report **per model** — Haiku, Sonnet, Opus — never pooled. Uplift differs sharply by model, and the
per-model split is the whole point: the strategy is to distil from a frontier model so that **a small,
cheap model plus the skill approaches a large one**. A pooled average hides exactly the result you are
trying to demonstrate, and it is common for a skill to lift a small model materially while doing
nothing for a large one.

## Honest negatives

Publishing only wins makes the whole benchmark untrustworthy. Required:

- **List the cells where the skill does not separate.** A task both arms already pass is a *floor*, not
  a differentiator — it shows the task was too easy to discriminate, not that the skill is inert.
- **Never publish a cell where the skill moves a column backwards** without saying so plainly.
- **State the limits**: n per cell, which models ran, which did not, any cell that failed for
  environmental reasons.

When a benchmark shows no uplift, the honest response is a harder fixture — not a kinder metric.

## Retry parity

In real use a skill runs inside a loop that retries: a verifier panel catches a first attempt that got
it wrong and sends it back, and a large share of the practical benefit comes from that retry. A
benchmark arm is usually a single pass.

**A single-pass arm and a panel-retry arm measure different systems.** So:

- **State per arm** whether it ran single-pass or under a retrying panel. An undisclosed mismatch is
  the difference between "this skill does nothing" and "we measured the wrong thing".
- Expect a single-pass benchmark to **understate** any skill whose value shows up under retry.
- Do not re-baseline a published number to look better. Add the `skill+panel` arm, re-measure, and
  report both.

## Burned fixtures

Once every arm has seen an answer key, that fixture can no longer measure the next round of
improvement — you would be scoring against material the skill was tuned on, and the number goes up
without the skill getting better.

Author a **new fixture, blind to the skill text**, before each improvement round. Record which fixtures
are burned, so a later round does not quietly reuse one.

## Procedural skills: execute against a real artifact

Some skills do not *generate* an artifact in one shot — they instruct an agent to perform a multi-step
task on a real one: migrate a framework version, refactor a module, scaffold infrastructure, rebuild a
repo. For these the single-call "skill as system prompt, input as user message" harness is the wrong
instrument. Evaluate by **execution against a real artifact with an objective real-world metric**, and
the artifact's own ground truth replaces the judge — cheaper, deterministic, and a far stronger signal
than scoring prose.

1. **Pick a test repo where "correct" has a ground-truth signal** — ideally one where a correct
   application is a *no-op against a captured baseline*. A Tailwind v3→v4 migration should be visually
   identical, so committed golden screenshots become the gold standard and the metric is pixel-parity
   plus `build`/`lint`/`typecheck`/tests passing plus a static "residual v3 markers = 0" grep.
2. **Capture the baseline before drafting the skill.** Set the metric up on the unmodified repo, confirm
   it is deterministic (run it twice, expect identical output), and confirm the *un-migrated* state
   scores 0 — otherwise the gate does not discriminate and a passing score means nothing.
3. **`evaluate.sh` orchestrates: reset → fresh agent applies the skill → measure.** Reset the repo to the
   captured baseline, spawn a **fresh** agent told to perform the task following *only* the skill under
   test (no other guides, no builder context), then run the deterministic checks and emit `METRIC` lines.
4. **A fresh agent every run is the point.** It measures the skill's *self-sufficiency* rather than the
   builder's accumulated context. A clean reset between runs (`git reset --hard <baseline> && git clean
   -fd`, plus a reinstall) is mandatory or scores drift upward as state leaks between runs. Varying the
   executor model is a useful robustness check: if a smaller model plus the skill still hits the target,
   the skill carries its own weight.

This makes the tool-first pattern natural: have the skill run any deterministic tool — a codemod, a
formatter, a generator — for the mechanical bulk, and reserve its prose for the judgment the tool cannot
do. The real-world metric then verifies the whole.

## Where benchmarks live

`benchmarks/<skill-name>/`, at the repository root — **outside** the shipped skill directory, so a
benchmark never ships when someone installs the skill. Each is self-contained: arms, scorer, fixtures,
configs, and a `README.md` carrying **method · per-model results · reproduce steps**.

Arms resolve the skill by relative path (`../../skills/<name>/SKILL.md`), so they run from inside the
repository.

## Pre-publication checklist

- [ ] Bare arm and skill arm on identical tasks, same model, one variable
- [ ] Skill arm reads the shipped file
- [ ] Scorer self-test passes offline
- [ ] Deterministic key where possible; judges only where judgment is required
- [ ] n and models disclosed; medians reported per model
- [ ] Retry parity stated per arm
- [ ] Non-separating cells published
- [ ] Fixture recorded as burned
- [ ] Benchmark outside `skills/`, with method and reproduce steps in its README
