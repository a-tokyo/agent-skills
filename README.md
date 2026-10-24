# agent-skills

Reusable agent skills for AI coding assistants. Compatible with Cursor, Claude Code, Codex, and 40+ agents via [`npx skills`](https://skills.sh).

[![skills.sh](https://skills.sh/b/a-tokyo/agent-skills)](https://skills.sh/a-tokyo/agent-skills)

## Skills

`Download` grabs a ready-to-upload `.zip` of just that skill — for Claude Desktop (Settings →
Capabilities), which has no CLI and can't run `npx skills`. Rebuilt whenever a skill changes on
`main`.

| Skill | Description | Download |
|-------|-------------|----------|
| [production-grade](skills/production-grade/) | Principle-engineering posture for production-grade code. Plans before code, simplest-correct-solution-first, ACM-grade algorithms, EXPLAIN-first databases, idempotent-atomic writes, realtime-first, concurrent-by-default, TDD-steered E2E, runtime-coherent infrastructure, maintenance & remediation of inherited or generated code. Substrate-agnostic. | [.zip](https://github.com/a-tokyo/agent-skills/releases/download/skills-latest/production-grade.zip) |
| [tailwind-v3-to-v4-migration](skills/tailwind-v3-to-v4-migration/) | Migrate a project from Tailwind CSS v3 to v4 safely and completely. Codemod-first (`@tailwindcss/upgrade`), then the judgment it can't do: dependency/PostCSS/Vite/CLI plumbing, JS config → CSS `@theme` (or keep via `@config`), the v4 changed-defaults audit + compat shims, a residual sweep, and proving the migration is a visual no-op. Framework-agnostic; bundles the official upgrade guide. | [.zip](https://github.com/a-tokyo/agent-skills/releases/download/skills-latest/tailwind-v3-to-v4-migration.zip) |
| [create-skill-autoresearch](skills/create-skill-autoresearch/) | Factory for building production-grade agent skills: interviews you for purpose + gold standards, researches the domain (gold standards win where docs disagree), drafts the skill with Anthropic's `skill-creator`, autonomously improves it against an LLM-as-judge (or real-world) metric, gates it with a with/without `claude plugin eval`, and verifies it with an independent multi-agent panel — delegating to `tribunal` when installed. Ships a same-model-uplift benchmark standard (scored by execution, honest negatives required) and a publish checklist, and stops the improvement loop on a plateau inside judge variance. Composes its companions instead of copying them (`skill-creator`, `writing-for-agents`, `autoresearch`, `premortem`, `handoff`), with a built-in fallback for standalone installs; the full batteries-included environment is the [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness). | [.zip](https://github.com/a-tokyo/agent-skills/releases/download/skills-latest/create-skill-autoresearch.zip) |
| [tribunal](skills/tribunal/) | Doer → verifier-panel → consensus delivery verification for any artifact (code slices, plans, documents, audits). An orchestrator freezes acceptance criteria before implementation, dispatches a doer, then convenes a context-walled panel of independent verifiers — including an adversary with a must-oppose mandate — for evidence-anchored review (citations grepped before consensus math) adjudicated to SHIP / SHIP_WITH_CAVEATS / ITERATE / BLOCK / ESCALATE. Principles-first: panel lenses, dimensions, and prompts derived per artifact from hard invariants. Platform-agnostic; degrades to sequential fresh-context sessions without subagents. Outcome-weighted A/B benchmarks (blind-judged, executed answer keys, honest negatives included) in its README. | [.zip](https://github.com/a-tokyo/agent-skills/releases/download/skills-latest/tribunal.zip) |
| [database-documentation](skills/database-documentation/) | Generate database documentation that reaches **provable 100% parity** with the real schema. Grounds every statement in the live database (system-catalog introspection) and cross-validates against ORM models, migrations, generated types, and seeds, then proves completeness with a mechanical count gate + identity-diff (re-introspect and diff until empty). Emits mermaid ERDs, per-table data dictionaries, and a machine-readable `schema.json`. Engine-agnostic (PostgreSQL, MySQL, SQL Server, SQLite) and ORM-agnostic (Prisma, TypeORM, Drizzle, Sequelize, Knex, Django, Rails). ≥3 context-walled adversaries (omission / hallucination / correctness) gate the result; platform-agnostic with graceful degradation. Reproducible dual-dialect benchmark with honest negatives in its README. | [.zip](https://github.com/a-tokyo/agent-skills/releases/download/skills-latest/database-documentation.zip) |
| [app-ai-guardrails](skills/app-ai-guardrails/) | Scaffold a new production application with the full agentic-AI guardrail canon baked in from commit #1: a uniform 7-gate interface (lint, typecheck, test, coverage, build, e2e, audit) on each stack's native runner, strict types, maximal static analysis, coverage thresholds with teeth, pre-commit hooks, hardened CI, supply-chain pinning, and an agent-ready `AGENTS.md` — every gate verified green before the first commit. Native adapters for Next.js, NestJS, Django, Go, Rust, and Spring Boot, plus a discovery method for any other stack. Greenfield only (v1); retrofitting an existing repo is v0.2. Same-model uplift benchmark (bare vs with-skill, per-stack medians, honest negatives) in its README. | [.zip](https://github.com/a-tokyo/agent-skills/releases/download/skills-latest/app-ai-guardrails.zip) |

## Benchmarks

Skill benchmarks live in [`benchmarks/`](benchmarks/), one directory per benchmarked skill — kept outside `skills/`
so they don't ship when a skill is installed. Each is self-contained (arms, scorer, configs) and
measures the **same model with the skill vs without**, scored by executing the output rather than
reading it, with honest negatives included. See [`docs/benchmarking.md`](docs/benchmarking.md) for the
standard and [`benchmarks/README.md`](benchmarks/README.md) for the index.

**Arms are single-pass unless a benchmark's README says otherwise.** In real use these skills run inside
a retrying verifier panel, and a good share of the practical benefit comes from that retry — so read a
single-pass number as a floor rather than a ceiling.

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
npx skills add a-tokyo/agent-skills --skill database-documentation
npx skills add a-tokyo/agent-skills --skill app-ai-guardrails
```

> `create-skill-autoresearch` composes companion skills at runtime — `skill-creator`
> ([anthropics/skills](https://github.com/anthropics/skills)), `writing-for-agents` and `handoff`
> ([mattpocock/skills](https://github.com/mattpocock/skills)), `autoresearch`
> ([github/awesome-copilot](https://github.com/github/awesome-copilot)), and `premortem`
> ([parcadei/continuous-claude-v3](https://github.com/parcadei/continuous-claude-v3)) — and runs
> `claude plugin eval` when Claude Code is available. Install the companions too
> (`npx skills add https://github.com/anthropics/skills --skill skill-creator`, …), or use the
> batteries-included [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness) where
> everything is wired up. Without them the factory falls back to its built-in checklist.

### Claude Code plugin

Alternative to `npx skills` for Claude Code / IDE users — install via the native plugin system:

```
/plugin marketplace add a-tokyo/agent-skills
/plugin install production-grade@a-tokyo-skills
```

Or install every skill at once:

```
/plugin install all-skills@a-tokyo-skills
```

The plugin install of `create-skill-autoresearch` (and `all-skills`) also adds the factory's kill guard,
a `PreToolUse` hook ([`hooks/kill_guard.py`](skills/create-skill-autoresearch/hooks/kill_guard.py), run by
`hooks/kill-guard` with `python3` or `python`). While the plugin is enabled it blocks Bash calls
containing `pkill`, `killall` or a pgrep-fed `kill` in every session, because a name-pattern kill also stops
sibling builds and your own processes; `kill <pid>` still works. It also adds the subagent guard and the finish
guard, which act only inside a factory build: the first denies a background subagent dispatch, the second sends an
unfinished build back when its turn ends ([hosts.md](skills/create-skill-autoresearch/references/hosts.md)). An `npx skills` install copies the hook files but never runs them.

`create-skill-autoresearch` runs on any agent that loads `SKILL.md` — Claude Code, Cursor, Codex,
OpenCode (`npx skills add a-tokyo/agent-skills --skill create-skill-autoresearch -a <host>`), on macOS,
Linux or WSL (on native Windows, run it inside WSL). Its
[`references/hosts.md`](skills/create-skill-autoresearch/references/hosts.md) gives each host's headless
command, subagents and kill-guard install: the skill directory is also a Cursor plugin
(`.cursor-plugin/plugin.json`), and it ships an OpenCode plugin and a Codex hook snippet. Without Claude
Code, the eval gate runs its manual with/without protocol and calls no `claude` command.

## License

MIT [Ahmed Tokyo](https://www.ahmedtokyo.com)
