# benchmarks

Benchmarks for the skills in this repo, one directory per skill. They live here, outside `skills/`, so
they **don't ship** when a skill is installed (`npx skills add …` pulls only the skill folder).

| skill | benchmark |
|-------|-----------|
| [production-grade](production-grade/) | same-model uplift (with skill vs without): engineering rigor, code size, correctness |
| [tribunal](tribunal/) | same-model A/B (tribunal panel vs single pass): cross-file defect recall, build-and-verify composite, + deterministic operative-skill propagation-fidelity and artifact handoff-durability checks |
| [app-ai-guardrails](app-ai-guardrails/) | same-model uplift (with skill vs bare) scaffolding a greenfield repo: deterministic `guardrail_score` + `all_gates_pass` across 6 stacks (Next.js, NestJS, Django, Go, Rust, Spring Boot), with teeth probes and honest env-failure/thin-cell disclosure |
| [create-skill-autoresearch](create-skill-autoresearch/) | end-to-end factory A/B/C (bare vs official skill-creator vs full 5-phase factory) building the same skill from one brief; the produced skill is executed on held-out cases and scored against a deterministic answer key |
| [database-documentation](database-documentation/) | dual-dialect reproducible benchmark: schema-parity of generated documentation against a live database, with honest negatives |

`tailwind-v3-to-v4-migration` has no benchmark yet — the only shipped skill without one.

**An arm is single-pass unless its benchmark README states otherwise** — no retrying verifier panel of
the kind real usage puts a skill inside — so a single-pass number understates any skill whose value
shows up under retry. A `skill+panel` arm is the fix; a re-scored number is not, and nothing here has
been re-scored.

Each benchmark is self-contained — arms, a scorer, configs, and a README with method, per-model
results, and reproduce steps. The arms read the skill they test from `../../skills/<skill>/SKILL.md`,
so run them from inside the repo.
