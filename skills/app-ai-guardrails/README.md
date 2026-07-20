# app-ai-guardrails

Scaffold a new production application with the full agentic-AI guardrail canon baked in from commit
#1. The skill wires a uniform 7-gate interface on each stack's native runner, runs every gate green,
proves the tree is clean, and commits — so a greenfield repo that AI agents will build in starts
strict, tested, and enforceable, not "we'll tighten it later."

## What it produces (per stack)

Every scaffold gets the same 7 **gates** — `lint`, `typecheck`, `test`, `coverage`, `build`, `e2e`,
`audit` — invoked identically enough that an agent new to the repo can always run them:

| Stack | Runner | Test / coverage | Lint maximalism | Audit |
|---|---|---|---|---|
| Next.js | `npm run <gate>` | vitest v8, 4-axis thresholds | eslint + sonarjs + unicorn | npm audit |
| NestJS | `npm run <gate>` | vitest v8 (SWC), 4-axis | eslint + sonarjs + unicorn | npm audit |
| Django | `uv run poe <gate>` | pytest-cov, blended `--cov-fail-under` | ruff `ALL` + mypy `--strict` | uv audit |
| Go | `just <gate>` | `go test` + coverage-gate script | golangci-lint v2 curated-broad | govulncheck |
| Rust | `cargo <gate>` | cargo-llvm-cov, 3-axis | clippy all+pedantic+nursery | cargo deny |
| Spring Boot | `./gradlew <gate>` | JUnit 5 + JaCoCo, 4-axis | Checkstyle + PMD (cognitive complexity) | OWASP dependency-check |

Plus: strict types, cognitive/cyclomatic complexity capped at 15, pre-commit hooks, a hardened CI
workflow (SHA-pinned actions, least-privilege permissions) with optional SonarCloud, toolchain
pinning, and an agent-ready `AGENTS.md` + `CLAUDE.md` + skills install set. Coverage gates have
**teeth** — they fail the build below the floor, they don't just report.

## Adapter depth (honest tiers)

- **Deep, benchmarked:** Next.js — full templates, live-verified fixes.
- **Solid, benchmarked:** NestJS, Django, Go, Rust — per-stack templates from verified research.
- **Solid, benchmarked:** Spring Boot — per-stack templates from live-verified research
  (Initializr probe, Maven Central/Gradle Plugin Portal, a real `./gradlew` run); benchmarked
  at sonnet n3, median 89, all gates green.
- **Discovery, unbenchmarked:** any other stack — the skill derives the mechanisms from live docs
  and demonstrates gate teeth once per gate to verify the fresh mapping. It has no benchmark median;
  the run's report says so.

## Prerequisites

- Network access (scaffolders, package registries, docs) and git.
- Node ≤24 for the JS stacks (keeps corepack available).
- Stack tools on PATH where the ecosystem has no auto-install: Go — `golangci-lint`, `just`,
  `lefthook`; Rust — `cargo install cargo-llvm-cov cargo-deny --locked`, `lefthook`; Django — `uv`;
  Spring Boot — JDK 21 (`JAVA_HOME` discovered via the adapter's ladder, e.g.
  `/usr/libexec/java_home -v 21` on macOS — not a hardcoded path), `lefthook`; a free NVD API key
  (`NVD_API_KEY`) is recommended for a fast `audit` gate. Missing tools are offered for install at
  Phase 0; declining aborts cleanly (greenfield has no partial success).
- **Go adapter needs a POSIX shell** (its coverage gate is a `sh`+`awk` script) — on Windows use
  Git-Bash or WSL; native PowerShell/cmd cannot run it.
- Optional: `context7` and `gh` (both have documented fallbacks — the skill never requires them).

## SonarCloud setup (one-time, human)

CI is green day-1 without any of this (the sonar job is gated on `SONAR_ENABLED`). To turn it on:

1. Create the SonarCloud project.
2. **Disable Automatic Analysis** (it conflicts with CI-based analysis).
3. Set New Code = your reference branch.
4. Add the `SONAR_TOKEN` repo secret; set repo variable `SONAR_ENABLED=true`.
5. Mark the quality gate a required branch-protection check.

The scaffold ships the `sonar-project.properties` and the CI job wired; these five steps are what
only a human with org access can do.

## Ubicloud runners (optional, ~80% cheaper CI)

Swapping `runs-on: ubuntu-latest` for `ubicloud-standard-2` cuts CI cost ~80% (5x). It requires one
org-level step the skill cannot perform:

1. Create a Ubicloud account at console.ubicloud.com and add billing.
2. Under "GitHub Runners" → "Connect New Account", install and authorize the **Ubicloud Managed
   Runners GitHub App** (org or repo scope).
3. Workflows then just use the `ubicloud-standard-2` label — no further repo config.

## Scope

Greenfield only. **Retrofitting** the canon onto an existing codebase — or scaffolding a new
package/service **inside an existing monorepo** (every mechanism assumes repo-root ownership: hooks,
CI, commit #1, `.claude/` all at root) — is **out of scope** (an agent under a "make gates green"
mandate inside real code can weaken tests/code to pass; greenfield bounds that blast radius). This
skill is not for LLM-safety/content-moderation guardrails, nor for adding a single tool to an
existing project.

## Benchmark

The benchmark (same-model uplift: skill vs bare; lint/typecheck/test/coverage/build gates executed,
e2e/audit verified config-present — see the benchmark README for exact semantics) lives in the
agent-skills `benchmarks/` tree, not in this skill folder. The published medians were earned **on a
machine meeting the prerequisites above** (the toolchain present, network reachable) — a first run
on a machine missing a stack's tools hits the Phase 0 preflight, not the median.

## Supported harness

The phase discipline assumes a harness that loads `SKILL.md` plus its on-demand `references/` files
(the Claude-Code skill-loading model the benchmarks ran under). A runtime that surfaces only the
frontmatter description will improvise the canon and produce exactly the marker-over-behavior repos
the gates are designed to catch — verify skill loading with a discovery probe before trusting a new
harness.
