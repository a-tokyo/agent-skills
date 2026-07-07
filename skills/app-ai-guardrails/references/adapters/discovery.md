# Adapter — discovery (the method for unknown stacks)

Load this when no native adapter (Next, Nest, Django, Go, Rust, Spring Boot) fits. It replaces the adapter file
in the phase system: the same phases 0–7 run, but you first derive the stack's mechanisms yourself
instead of transcribing them from a native adapter. Every mechanism comes from live docs
(context7/WebFetch), never training recall.

## Steps

| Step | Action | Completion criterion |
|---|---|---|
| **D-1 Fingerprint** | Identify language / framework / package manager from manifests, lockfiles, and the official scaffolder's docs. | Stack named AND a scaffold approach chosen via the currency ladder (never recall): the official scaffolder, **or**, if the ecosystem has none (e.g. plain C), the write-files-directly precedent from `references/adapters/go.md`. |
| **D-2 Map the canon** | For EVERY row of the canon table — gate interface · strict types · lint maximalism · coverage + teeth · e2e · hooks · supply chain · CI + sonar · agent surface — find the stack's native mechanism via docs. | Mapping table complete: **every canon row filled, or marked "no native equivalent — honest gap."** Fabricating an equivalent is the failure mode. |
| **D-3 Pick the runner** | Use the most-native task mechanism (the gate-interface principle); a justfile ONLY if nothing native carries tasks. | All 7 gate names wired on the chosen runner. |
| **D-4 Execute-verify** | Run phases 1–7. Additionally, for EVERY mapped gate: run it green AND demonstrate teeth once — inject one violation, observe a non-zero exit, **revert immediately**. | Gate ledger (Phase 6) plus a teeth check per gate. |

D-4 adds the teeth self-check that native adapters skip: their mappings are already benchmark-verified,
but a fresh discovery mapping is unverified by construction, so demonstrating teeth once per gate is
how you verify the mapping itself. Inject exactly one violation, observe the non-zero exit, and revert
it immediately — greenfield-only scope bounds the blast radius.

## Honest limits (state these in the run's report)

- **Never benchmarked.** All six native adapters have benchmark medians;
  discovery does not. Its only
  quality evidence is the tribunal Utility verifier run.
- **Thresholds may need per-toolchain calibration.** Use the nearest analog from
  `references/canon/coverage.md` and say which one you borrowed.
- **Sonar support for the language may not exist.** Check; wire it or omit it honestly — never claim
  coverage wiring you could not verify.
