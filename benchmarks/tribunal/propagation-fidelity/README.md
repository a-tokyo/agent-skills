# Propagation-fidelity eval

`recall-task/` and `build-task/` measure verification **quality**. They exercise
dispatch but never assert that an **operative skill** (a domain/quality skill the
orchestrator is running under, e.g. `production-grade`) actually reaches the doer and
the panel. Tribunal `v0.0.2` added that propagation; this eval is its dedicated test.

## What it asserts

Given the prompts the orchestrator actually dispatched, for operative skill `S`:

1. **Doer** — the doer prompt names `S`, instructs loading it, and carries a degrade
   note (what to do if it can't load).
2. **Panel** — every verifier prompt does the same in its `Inputs`.
3. **No nesting** — no dispatched prompt instructs a subagent to load/run the
   **tribunal** skill itself (a bare "you are the doer in a tribunal run" is fine;
   "load the tribunal skill and convene your own panel" is a violation).

The check is **deterministic** — string/structure assertions on the prompts, no LLM
judge, no network.

## Run

```bash
# 1. capture a real dispatch: run tribunal on task/ with production-grade as the
#    operative skill, and save each dispatched prompt verbatim into a dir:
#       <dispatched>/doer.txt
#       <dispatched>/verifier-<lens>.txt   (one per verifier)
#    (task/run-prompt.md is the operator prompt that produces these.)

# 2. check it:
node check.mjs <dispatched> --skill production-grade
```

`check.mjs` prints `METRIC propagation_fidelity=1|0` (plus per-role metrics) and exits
non-zero on any failure.

## Self-test (offline, no API)

```bash
node selftest.mjs
```

Confirms the checker PASSes a correctly-propagated dispatch (`fixtures/pass/`) and
FAILs the two seeded regressions:
- `fixtures/fail-doer-missing/` — the **pre-`v0.0.2`** doer prompt (no operative
  skill). The checker failing this is the before/after that proves the skill edit
  matters.
- `fixtures/fail-nesting/` — a doer told to load the tribunal skill on its own output.

## Before / after

Run the checker against a tribunal dispatch produced **with the pre-`v0.0.2` skill**
(doer/verifier prompts omit the operative skill) → FAIL. Re-run against a dispatch
produced with `v0.0.2` → PASS. That delta, not a quality score, is what this eval
measures.
