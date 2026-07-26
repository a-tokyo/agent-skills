# Benchmark — tribunal

Measures the skill the way a skill should be measured: the **same model, with the
tribunal pattern vs. a single pass**, on identical tasks, scored by executing the
answer key (not by assertion). Tribunal is a multi-agent orchestration skill, so a run
is **agent-orchestrated** — a doer and a context-walled verifier panel dispatched as
real subagents — not a single `promptfoo` command. Three suites:

| suite | what it measures | scoring |
|-------|------------------|---------|
| [`recall-task/`](recall-task/) | cross-file defect recall on a 6-module TypeScript codebase (cause in one file, failure in another) + correct verdict + zero false positives | deterministic recall vs `ANSWER-KEY.md` (O1/O2) + blind LLM judge for process (P1–P7), per [`judge-rubric.md`](judge-rubric.md) |
| [`build-task/`](build-task/) | build-and-verify a 3-slice CLI against a 17-criterion spec; catch a seeded spec contradiction | deterministic AC re-execution (`JUDGING.md`) + blind judge for process |
| [`propagation-fidelity/`](propagation-fidelity/) | does an **operative skill** (e.g. `production-grade`) actually reach the doer and panel, and is the tribunal skill never nested? | fully deterministic — string/structure checks on the dispatched prompts, self-tested offline |
| [`handoff-durability/`](handoff-durability/) | is the **artifact** handed over by a fetchable address rather than a working-tree path, and do the round index and doer budget travel in the handoff? | fully deterministic — string/structure checks on the dispatched prompts and the doer's report, self-tested offline |

## Method

- **Same-model A/B:** identical task and model on both arms; the only variable is
  whether the tribunal pattern (independent panel) is used vs. a single pass. No
  model-tier confound.
- **Blind judging:** candidate reports are sanitized (skill names/paths removed),
  labelled and order-randomized, and scored independently against the rubric before any
  comparison; deterministic items (recall, AC re-execution, verdict match) are computed
  mechanically first and may not be overridden. Sealed A/B mappings live only in the
  dev harness and are **not** vendored here.
- **Models:** a small model (Haiku-class) for the lift measurement, with Sonnet- and
  Opus-class single passes as frontier references. Per Anthropic's agent-skills
  best-practices, the skill is exercised across tiers because a skill that helps a
  frontier model may under-guide a small one — and here the gains concentrate on the
  smaller model.

## Results

These figures are ported from the harness judge reports (`recall-task/JUDGE-REPORT.md`,
`build-task/JUDGE-REPORT.md`).

### Cross-file verification — tier-weighted defect recall (small model)

| Run | Recall |
|---|---|
| Single pass (no skill) | 0.62 |
| Single-session "panel" (roles simulated in one context) | 0.62 |
| **Independent panel (tribunal)** | **0.75** (mean of n=3; range 0.69–0.83) |
| Frontier single pass (Sonnet-class), reference | 0.79 |

The independent panel lifts the small model from its 0.62 floor toward the frontier
single-pass reference. The single-session "panel" gives **zero** lift (0.62 = the
floor): **independence is the load-bearing factor**, not the label.

### Build-and-verify — composite / 10 (17-criterion CLI spec)

| Run | Composite | Notable |
|---|---|---|
| **With tribunal** | ~9.5 | caught and escalated a spec contradiction |
| Single pass (no skill) | 8.35 | shipped the same contradiction silently |

### Propagation fidelity (tribunal ≥ v0.0.2)

Deterministic, not a score: the operative skill reaches the doer and every verifier
with a load + degrade instruction, and the tribunal skill is never forwarded
(no nesting). See `propagation-fidelity/`.

Live before/after (same model orchestrating, `production-grade` as operative skill,
prompts emitted then checked blind):

| Skill version | doer | verifiers (of 3) | no nesting | `propagation_fidelity` |
|---|---|---|---|---|
| pre-`v0.0.2` | partial (named, no degrade note) | **0 / 3** | ✓ | **FAIL** |
| `v0.0.2` | ✓ (name + load + degrade) | **3 / 3** | ✓ | **PASS** |

Under the pre-edit skill, even a capable orchestrator carried the standard to the doer
only and to **none** of the panel — so the panel would score against a weaker bar than
the work was built to. That is the exact gap the change closes.

### Handoff durability (tribunal ≥ v0.0.3)

Deterministic, not a score: the doer is told to materialize the artifact durably and
report a **fetchable address**; that address — not a working-tree path — reaches every
verifier; and the round index plus remaining doer budget travel in the handoff rather
than in orchestrator context. See `handoff-durability/`.

Production provenance for the failure mode: on a live detached run a doer completed its
slice but left the spec uncommitted, and the orchestrator had to catch it and re-dispatch
([lqa-app#3506](https://github.com/LeadingQuality/lqa-app/pull/3506)). On a shared
filesystem that is fragile; under a torn-down or namespace-isolated sandbox the panel
would score a path it cannot open.

Live before/after, same model and task in both arms, the skill version the only variable
(counts are runs passing each metric):

| Skill version | model | materialize | address reported | verifiers carrying it | budget carried | `handoff_durability` |
|---|---|---|---|---|---|---|
| `v0.0.2` | haiku (n=3) | 0/3 | 0/3 | 0/3 | 0/3 | **0/3** |
| `v0.0.3` | haiku (n=3) | 2/3 | **3/3** | **3/3** | 0/3 | 0/3 |
| `v0.0.2` | sonnet (n=3) | 1/3 | 1/3 | 0/3 | 1/3 | **0/3** |
| `v0.0.3` | sonnet (n=3) | **3/3** | **3/3** | **3/3** | **3/3** | **3/3** |

**No cell regresses**; every metric is equal or better under `v0.0.3`.

The load-bearing figure is `v0.0.2`/sonnet **propagation 0/3**: under the old skill a
capable orchestrator sometimes had its doer commit and report a SHA unprompted (1/3 on
both) and *still* pointed the panel at a working tree every single time. That is
invariant 1's latent flaw — "new-file paths" assuming a shared filesystem — reproduced
under controlled conditions, and `v0.0.3` takes it to 3/3.

Honest negative: `budget_carried` is 0/3 on haiku in **both** arms, so haiku shows no
composite PASS despite its address chain going 0/3 → 3/3. Invariant 6's "every dispatch
states the round index and the remaining budget" under-guides smaller models; sonnet
complies 3/3, haiku not at all. Reported as a finding rather than fixed by relaxing the
metric.

`n=3` per cell is small and the metrics are binary — these support "the address reliably
reaches the panel under `v0.0.3` and unreliably under `v0.0.2`", not a precise rate. The
arms run on haiku and sonnet, the tiers where a skill is load-bearing (same reasoning as
the suites above: a skill that helps a frontier model may under-guide a small one). All 12
attempted captures scored: no env failures, no extraction failures. Full per-run data and
method: [`handoff-durability/`](handoff-durability/).

## Reading the numbers

Honest caveats, straight from the harness analysis:

- **n is small and recall is noisy.** With 6 cross-file defects each caught by a
  3-reader union at ~50–60%, the count is Binomial-noisy (±~0.12 normalized). `0.75`
  is the n=3 mean; an earlier single high draw hit 0.81 — the honest claim is
  "approaches the frontier single pass," not "matches it."
- **The lift concentrates on smaller/cheaper models and multi-part work.** On a
  frontier model already strong at single-pass review the margin is small (+0.06–0.09);
  the hardest composition defects elude both arms.
- **The robust, tier-independent wins:** the correct verdict every time, **zero
  hallucinated / false-positive findings** (every cited line is grep-checked; refuted
  scores are dropped, not averaged), and catching *some* cross-class defects a single
  pass misses. It is NOT "makes a frontier model dramatically better."

### Scope of these numbers vs. the v0.0.2 change

The recall/build numbers were measured before `v0.0.2`'s operative-skill propagation.
That change is **additive** (it forwards more context to subagents) and the benchmark
fixtures don't involve an operative skill, so it isn't expected to move recall/composite
— but those suites do **not** test it. The `propagation-fidelity/` eval is the targeted
test for the new behavior; re-running recall/build for regression is a follow-up.

### Scope of these numbers vs. the v0.0.3 change

Same shape, same honesty. `v0.0.3`'s handoff invariants are additive — the doer is asked
for an address in addition to the diff, and the counters are restated in each dispatch —
and none of the recall/build fixtures involves a second sandbox, so the change is not
expected to move recall/composite. Those suites were **not** re-run, and no figure above
has been restated or softened to accommodate the new version. `handoff-durability/` is
the targeted test; re-running recall/build for regression remains a follow-up.

Note the arms differ from the recall/build suites: `handoff-durability/` and
`propagation-fidelity/` both A/B **one skill version against another** rather than skill
against no skill, because both measure an additive protocol change that has no bare-arm
counterpart.

## Reproduce

```bash
# Deterministic, offline — no API key needed:
node propagation-fidelity/selftest.mjs          # the checker is sound
node propagation-fidelity/check.mjs <dispatched> --skill production-grade
node handoff-durability/selftest.mjs            # checker + dispatch extractor are sound
node handoff-durability/check.mjs <dispatched>

# Live handoff-durability arms (needs a headless credential; see that suite's README):
handoff-durability/arms/run-arm.sh v003 sonnet v003-sonnet-1

# Quality suites (agent-orchestrated, needs an agent with parallel subagents):
#  1. recall-task: give an agent recall-task/prompt.md + recall-task/spec.md +
#     recall-task/artifact/; have it run the tribunal pattern; score its report against
#     recall-task/ANSWER-KEY.md + judge-rubric.md (deterministic O1/O2 first, then a
#     blind judge for P1–P7).
#  2. build-task: give an agent build-task/prompt.md + build-task/spec.md; re-execute
#     the delivered CLI against build-task/JUDGING.md (17 ACs); judge process blind.
#  Run each arm (single pass vs tribunal panel) on the same model; compare.
```

Prereqs for the quality suites: an agent runtime that can dispatch parallel,
context-walled subagents (the `tribunal` skill installed), and (recall-task) Node +
TypeScript to type-check the fixture. The propagation-fidelity checker is plain Node,
zero dependencies.

## Provenance

Vendored from the development harness
(`agent-skills-harness/builds/tribunal/work/evaluation/benchmark/`, gitignored scratch)
into this tracked location so the benchmark is reproducible and version-controlled.
Maintainer-local materials (sealed A/B mappings, `node_modules`, calibration scratch)
are intentionally excluded.
