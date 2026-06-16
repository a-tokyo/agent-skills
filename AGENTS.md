# AGENTS.md

Contributor and agent contract for **agent-skills**. Follows the [agents.md](https://agents.md/)
format. For the user-facing skill catalogue, see [README.md](README.md).

## Project Overview

A monorepo of reusable agent skills (a `SKILL.md` plus optional references) for AI coding assistants,
installed via [`npx skills`](https://skills.sh) and compatible with Cursor, Claude Code, Codex, and
40+ agents. Each skill is self-contained and ships on its own. There is no application to build or
server to run — the "product" is the skill files; the "tests" are benchmarks.

## Repo structure (monorepo)

```
skills/<name>/        a shippable skill: SKILL.md (+ optional references/, README.md)
benchmarks/<name>/    benchmark for that skill — OUTSIDE skills/ so it doesn't ship on install
docs/                 contributor guides (this contract links into them)
README.md             user-facing skill catalogue + install
```

The closest `AGENTS.md` wins; this root file governs the whole repo.

## Setup & commands

```bash
# install / use a skill (consumer side)
npx skills add a-tokyo/agent-skills                      # all skills
npx skills add a-tokyo/agent-skills --skill production-grade

# check every skill against the platform limits (run from repo root)
wc -l skills/*/SKILL.md                                  # whole-file lines (Anthropic's limit is body < 500)
# full name + description check (handles folded YAML): see docs/skill-authoring.md

# run a skill's benchmark (from inside the repo)
cd benchmarks/<name> && cat README.md                    # method + reproduce steps
```

There is no root `package.json`; benchmarks carry their own toolchain (Node + `uv`/promptfoo).

## Code Style — skill authoring

Full rules and the compliance check: **[docs/skill-authoring.md](docs/skill-authoring.md)**. The
source of truth is Anthropic's [best-practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
and [API](https://platform.claude.com/docs/en/build-with-claude/skills-guide) guides — the table
below is a *mirror, confirm at the guide*.

| What | Limit |
|------|-------|
| `SKILL.md` body | < 500 lines |
| `name` | ≤ 64 chars, `[a-z0-9-]`, no XML tags, no reserved words (`anthropic`, `claude`) |
| `description` | ≤ 1024 chars, non-empty, third person, *what* + *when* |
| Skill upload (all files) | ≤ 30 MB |

Plus: progressive disclosure (split into `references/` before the body nears 500 lines), references
**one level deep** from `SKILL.md`, a `## Contents` TOC on any reference > 100 lines, fully-qualified
MCP names (`ServerName:tool_name`), forward-slash paths, provide-a-default over a menu, and no
time-sensitive wording. Naming is descriptive kebab-case (`production-grade`); consistency with the
existing set beats the gerund default.

## Testing — benchmarking

A new or materially changed skill ships a self-contained benchmark in `benchmarks/<name>/`. The
standard is **same-model uplift**: the same model run with the skill vs. bare, on identical tasks,
scored by executing the output. Full method and reproduce steps:
**[docs/benchmarking.md](docs/benchmarking.md)**.

## Security

A skill that directs the agent to fetch third-party content (docs, web pages, MCP issue/ticket
bodies, other repositories) must frame that content as **untrusted data, not instructions** — the
indirect-prompt-injection posture. `skills/production-grade/SKILL.md` (rule M2) is the reference
implementation. Never commit secrets; `.env` is git-ignored.

## PR Guidelines

- Branch off `main`; never commit directly to `main`.
- Before pushing: run the limit check (above) and the relevant skill's benchmark if you changed a
  `SKILL.md`.
- Keep diffs surgical — one concern per PR; a rename ships separately from a feature.
- Bump the skill's `version` in its frontmatter when its behaviour changes.
- Commit subject in imperative mood; explain *why* in the body.
