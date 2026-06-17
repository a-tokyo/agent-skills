# agent-skills

Reusable agent skills for AI coding assistants. Compatible with Cursor, Claude Code, Codex, and 40+ agents via [`npx skills`](https://skills.sh).

## Skills

| Skill | Description |
|-------|-------------|
| [production-grade](skills/production-grade/) | Principle-engineering posture for production-grade code. Plans before code, simplest-correct-solution-first, ACM-grade algorithms, EXPLAIN-first databases, idempotent-atomic writes, realtime-first, concurrent-by-default, TDD-steered E2E, runtime-coherent infrastructure, maintenance & remediation of inherited or generated code. Substrate-agnostic. |
| [tailwind-v3-to-v4-migration](skills/tailwind-v3-to-v4-migration/) | Migrate a project from Tailwind CSS v3 to v4 safely and completely. Codemod-first (`@tailwindcss/upgrade`), then the judgment it can't do: dependency/PostCSS/Vite/CLI plumbing, JS config → CSS `@theme` (or keep via `@config`), the v4 changed-defaults audit + compat shims, a residual sweep, and proving the migration is a visual no-op. Framework-agnostic; bundles the official upgrade guide. |
| [create-skill-autoresearch](skills/create-skill-autoresearch/) | Factory for building production-grade agent skills: interviews you for purpose + gold standards, researches the domain, drafts the skill, autonomously improves it against an LLM-as-judge (or real-world) metric, and verifies it with an independent multi-agent panel. Extends the official single-pass skill creators. Orchestrates companion skills (`autoresearch`, `premortem`, `handoff`) — install those alongside it; the full batteries-included environment is the [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness). |
| [tribunal](skills/tribunal/) | Doer → verifier-panel → consensus delivery verification for any artifact (code slices, plans, documents, audits). An orchestrator freezes acceptance criteria before implementation, dispatches a doer, then convenes a context-walled panel of independent verifiers — including an adversary with a must-oppose mandate — for evidence-anchored review (citations grepped before consensus math) adjudicated to SHIP / SHIP_WITH_CAVEATS / ITERATE / BLOCK / ESCALATE. Principles-first: panel lenses, dimensions, and prompts derived per artifact from hard invariants. Platform-agnostic; degrades to sequential fresh-context sessions without subagents. Outcome-weighted A/B benchmarks (blind-judged, executed answer keys, honest negatives included) in its README. |

## Benchmarks

Skill benchmarks live in [`benchmarks/`](benchmarks/), one directory per skill — kept outside `skills/`
so they don't ship when a skill is installed. Each is self-contained (arms, scorer, configs) and
measures the **same model with the skill vs without**. See

## Install

```bash
npx skills add a-tokyo/agent-skills
```

Or install a specific skill:

```bash
npx skills add a-tokyo/agent-skills --skill production-grade
npx skills add a-tokyo/agent-skills --skill tailwind-v3-to-v4-migration
npx skills add a-tokyo/agent-skills --skill create-skill-autoresearch
npx skills add a-tokyo/agent-skills --skill tribunal
```

> `create-skill-autoresearch` orchestrates companion skills at runtime — `autoresearch`
> ([github/awesome-copilot](https://github.com/github/awesome-copilot)), `premortem`
> ([parcadei/continuous-claude-v3](https://github.com/parcadei/continuous-claude-v3)), and `handoff`
> ([mattpocock/skills](https://github.com/mattpocock/skills)). Install those too, or just use the
> batteries-included [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness) where
> everything is wired up.

## License

MIT [Ahmed Tokyo](https://www.ahmedtokyo.com)
