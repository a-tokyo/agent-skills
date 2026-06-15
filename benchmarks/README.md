# benchmarks

Benchmarks for the skills in this repo, one directory per skill. They live here, outside `skills/`, so
they **don't ship** when a skill is installed (`npx skills add …` pulls only the skill folder).

| skill | benchmark |
|-------|-----------|
| [production-grade](production-grade/) | same-model uplift (with skill vs without): engineering rigor, code size, correctness |

Each benchmark is self-contained — arms, a scorer, configs, and a README with method, per-model
results, and reproduce steps. The arms read the skill they test from `../../skills/<skill>/SKILL.md`,
so run them from inside the repo.
