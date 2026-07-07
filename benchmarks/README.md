# benchmarks

Benchmarks for the skills in this repo, one directory per skill. They live here, outside `skills/`, so
they **don't ship** when a skill is installed (`npx skills add …` pulls only the skill folder).

| skill | benchmark |
|-------|-----------|
| [production-grade](production-grade/) | same-model uplift (with skill vs without): engineering rigor, code size, correctness |
| [tribunal](tribunal/) | same-model A/B (tribunal panel vs single pass): cross-file defect recall, build-and-verify composite, + a deterministic operative-skill propagation-fidelity check |
| [app-ai-guardrails](app-ai-guardrails/) | same-model uplift (with skill vs bare) scaffolding a greenfield repo: deterministic `guardrail_score` + `all_gates_pass` across 6 stacks (Next.js, NestJS, Django, Go, Rust, Spring Boot), with teeth probes and honest env-failure/thin-cell disclosure |
| [create-skill-autoresearch](create-skill-autoresearch/) | end-to-end factory A/B/C (bare vs official skill-creator vs full 5-phase factory) building the same skill from one brief; the produced skill is executed on held-out cases and scored against a deterministic answer key |

Each benchmark is self-contained — arms, a scorer, configs, and a README with method, per-model
results, and reproduce steps. The arms read the skill they test from `../../skills/<skill>/SKILL.md`,
so run them from inside the repo.
