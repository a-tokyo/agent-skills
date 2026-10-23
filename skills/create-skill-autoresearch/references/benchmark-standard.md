# Benchmark Standard

How to prove a finished skill earns its place. The autoresearch loop in Phase 4 optimizes *absolute*
output quality against gold standards; this is the separate question of whether the skill **changes
what the model does**. Answer it before shipping.

## Contents
- [The gate: same-model uplift](#the-gate-same-model-uplift)
- [Arms](#arms)
- [Choosing the evaluator](#choosing-the-evaluator)
- [Manual with/without protocol](#manual-withwithout-protocol)
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

## Choosing the evaluator

Four evaluators were compared on one task (six skills from bad to reference, two repeats, Haiku 5.5 as
executor and judge), against an answer-key scorer's ranking as ground truth
([full comparison](https://github.com/a-tokyo/agent-skills/blob/main/benchmarks/create-skill-autoresearch/EVALUATORS.md)):

| | Answer-key scorer | Plugin eval, `regex` graders | Plugin eval, `llm` grader | skill-creator eval |
|---|---|---|---|---|
| Rank agreement (Spearman) | 1.000 (test-retest) | 0.955 | 0.955 | 1.000 |
| Separates bad from good | 0.785 | 0.900 | 0.967 | 0.463 |
| Repeat-to-repeat drift | 0.012 | 0.036 | 0.022 | 0.017 |
| Reports uplift | no | yes | yes | yes |
| Cost per skill | $0.07 | $0.14 | $0.16 | $0.07 |

What the factory uses where:

- **Phase 4 metric** (3.4): the answer-key scorer whenever outputs are checkable — the most stable and
  cheapest, but absolute only. Without a checkable output, an LLM judge or skill-creator-style grading,
  read as a ranking.
- **5.0 gate** (3.6): `claude plugin eval` with deterministic graders (`regex`, `file_exists`) — the one
  method that both separates sharply and reports uplift. An `llm` grader only where no deterministic check
  exists: its 2-of-3 vote turns partly correct runs into fails (it scored a middling skill 0.20, truth 0.82).
- **Description work** (4.6): skill-creator. Its grader is lenient (a generic skill scored 0.54, truth
  0.14), so read its absolute scores as a ranking and its with/without Δ as the signal.

Limits: one task, six skills, two repeats, one model family as executor and judge.

## Manual with/without protocol

`scripts/gate.py` (phase-5.md 5.0) runs this instead of `claude plugin eval` whenever the build runs outside
Claude Code (`$CLAUDECODE` unset: another host targets its own model), `claude plugin eval --help` does not
exit 0, the skill is user-invoked (it never fires through the Skill tool, so plugin eval's with-arm measures
nothing), or `--backend manual --force-manual "<host>: <why>"` asks for it on another host whose shell inherits
`$CLAUDECODE` (a Claude Code build never passes `--backend manual`: for a model-invoked skill plugin eval can run,
`gate.py` refuses it). It calls no `claude` command. It runs the
gate gold standards on the pinned model:

- **Arms**: *with* gets the shipped `SKILL.md` as its system prompt; *without* gets no system prompt.
- **Runs**: 3 per case per arm.
- **Executor**: single-call (system prompt plus the case prompt, one completion: an API call, an SDK, or the
  host's headless CLI) when the skill only produces text; **agentic** (a fresh tool-using agent in a scratch
  copy of the case's fixtures, the skill directory beside them: the host's headless CLI from
  [hosts.md](hosts.md), e.g. `cursor-agent -p --force --trust`, `codex exec --skip-git-repo-check`,
  `opencode run`, `claude -p`) when the skill bundles `scripts/` or acts on files — one call cannot run a
  script or write a file; it runs with the factory's kill guard loaded (`CSA_EXECUTOR=1 claude -p --plugin-dir
  <skill-dir>`, [phase-3.md](phase-3.md) 3.4). Pass the choice to `gate.py --executor`.
- **Score**: each run is the fraction of the case's graders that pass (0-1), skill-fired indicators excluded:
  the 3.6 suite's graders where one exists, else the checks `run-case.sh` applies to the gold standard
  (deterministic first). Mechanical graders (`regex`, file checks) are applied directly. An `llm` grader is
  judged by the host's headless CLI with the judge model (`claude -p --model <judge>`, `cursor-agent -p
  --model <judge>`, `codex exec --skip-git-repo-check -m <judge>`, `opencode run -m <judge>`), the grader's
  body as the rubric and the run's output as the input, asking for PASS or FAIL; it passes on a majority of 3
  calls. `<judge>` is a model from a different family than the builder when the host offers one, else a small
  model of the builder's family.

The build implements one run in `work/evaluation/run-case.sh <case> <with|without>` (executable, run from
the workspace root, `GATE_MODEL`, `GATE_RUN` and `GATE_EXECUTOR` in its environment; `<case>` is a gate gold
standard's entry as data-split.yaml lists it, which `gate.py record` copies into `plugin_eval`): it executes the case on
that arm the way `$GATE_EXECUTOR` names (`single-call` or `agentic`), applies the graders, and prints
`EXECUTOR=<the executor it ran>` and `SCORE=<0..1>` — `gate.py` reads the last of each. The executor line is a
format check, self-attested by the adapter, not proof of what ran: a run that prints none, or another executor,
fails, so the label `uplift.json` records is one the adapter acknowledged, but an adapter that ignores
`$GATE_EXECUTOR` and echoes a constant still passes; the panel reads `run-case.sh` to check it. A non-zero exit, a timeout or a missing `SCORE` or `EXECUTOR` line is a
failed run, not a score of 0: `gate.py` retries it once, then records it under `errors` and marks the artifact
partial. Keep the adapter's scratch files outside `work/evaluation/`: a gate input that changes while the gate runs
leaves no finished run.

`gate.py` writes the result to `work/evaluation/uplift.json`; `delta` is mean(with) − mean(without),
read exactly as plugin eval's Δ:

```json
{
  "cases": [
    { "case": "holdout-03", "with": [1.0, 0.8, 1.0], "without": [0.6, 0.6, 0.4], "delta": 0.4 }
  ],
  "summary": { "model": "<model>", "executor": "agentic", "runs_per_case": 3, "in_sample": true,
               "mean_with": 0.93, "mean_without": 0.53, "mean_delta": 0.4 },
  "partial": false,
  "errors": []
}
```

`in_sample` is true when the gate cases are also training cases (3-9 gold standards, phase-4.md 4.2).

**The without arm is cached.** It never reads the skill, so a re-gate after a skill edit re-runs only the
*with* arm. `gate.py` stores each complete without arm in `work/evaluation/baseline-cache/<key>.json`, the key a
sha256 of the case, model, executor, `run-case.sh`, the case's suite directory and every gate input (gold
standards, fixtures, scorers, graders): change any of them and the cache misses. A reused case carries
`"without_cache": {"key": ..., "sha256": ...}` in `uplift.json`; `check_gate.py` recomputes the key, re-hashes
the entry and compares its scores, failing `stale_baseline` on any mismatch. `gate.py --fresh-baseline` re-runs
every without arm. Plugin mode caches nothing: `claude plugin eval --ablation` offers `none` or
`with-without`, no without-only arm.

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
