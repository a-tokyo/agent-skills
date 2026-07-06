# Gate interface — the 7-name contract

The stack-agnostic centerpiece: every guardrailed repo exposes the same 7 gate **names** on
its native runner, so an agent that has never seen the repo can always discover and run gates.

## Contents

- The invariant
- Runner map
- Zero-warnings per linter
- `format` auxiliary rule
- AGENTS.md contract snippet
- Anti-defang red flags
- Unknown-repo probe order

## The invariant

The 7 names are identical everywhere: `lint`, `typecheck`, `test`, `coverage`, `build`, `e2e`,
`audit`. Names are flat and colon-free (colons are `just`'s recipe-dependency separator, so
`test:coverage` would not parse as a single recipe — one name-set must be justfile-legal, and
npm/cargo/poe have no such restriction). Semantic per gate, not just "run the tool":

- `lint` — maximal ruleset, zero warnings tolerated (see zero-warnings table).
- `typecheck` — a check-only pass (`tsc --noEmit` / `mypy --strict` / `go vet` / `cargo check`).
  **Distinct from `build`** — it produces no artifact.
- `test` — unit tests, e2e excluded so local iteration stays fast.
- `coverage` — **enforce** thresholds and exit non-zero under the floor. Not report-only. This
  is the capexlog-vs-everyone gap.
- `build` — produce the artifact (`next build` / `collectstatic` / `go build` / `cargo build`).
- `e2e` — in-process or API end-to-end against the real app.
- `audit` — **fail closed** on advisories ≥ moderate.

## Runner map

Use each ecosystem's most-native task mechanism; a justfile only where nothing native carries
tasks (Go).

| Stack | Runner file | Invocation | Rationale |
|---|---|---|---|
| Next / Nest | `package.json` `scripts` | `npm run <gate>` | npm scripts already are the meta-runner; a justfile on top = two sources of truth |
| Django | `pyproject.toml` `[tool.poe.tasks]` | `uv run poe <gate>` | poe rides on uv (already required), zero extra CI binary, pyproject-native, Windows-safe |
| Go | `justfile` | `just <gate>` | Go has no native named-task mechanism and no dep-graph tool to carry a task-runner dev-dep |
| Rust | `.cargo/config.toml` `[alias]` | `cargo <gate>` | each of the 7 gates is a single cargo subcommand; aliases satisfy the contract 1:1 |
| Spring Boot | `build.gradle` `tasks.register(...)` | `./gradlew <gate>` | Gradle is Java's own most-native task mechanism — no justfile/poe needed, same principle as npm/cargo |

Invocation shape is already per-stack; the invariant that matters is the 7 **names**. An alias
expands to exactly one cargo subcommand, which is why every gate maps to one subcommand — no
alias is asked to chain two subcommands (the CI workflow does the sequencing). Spring Boot's
`build` gate is the one documented exception to task-independence: it is Gradle's own native
`build` lifecycle task (assemble+check), a superset of test/lint by ecosystem
convention rather than an independent artifact-only task — see `references/adapters/springboot.md`.

## Zero-warnings per linter

Tools differ in default strictness: some are permissive and need an explicit escalation flag;
others are strict-by-construction and the only failure mode is someone adding an escape hatch.

| Linter | Reaching zero-warnings-fails |
|---|---|
| ESLint (JS/TS) | explicit `--max-warnings=0` on the `lint` script (warn-severity findings otherwise pass) |
| clippy (Rust) | explicit `-D warnings` (lints are warn-by-default) |
| ruff (Python) | nothing extra — any violation exits 1 by construction; never pass `--exit-zero` |
| mypy `--strict` | nothing extra — any type error is an error |
| golangci-lint (Go) | nothing extra — exits non-zero on any issue; never pass `--issues-exit-code=0` |
| Checkstyle + PMD (Spring Boot) | nothing extra — both strict-by-construction (`ignoreFailures=false` default); Checkstyle also needs `maxWarnings=0` (permissive-by-default like ESLint) |

## `format` auxiliary rule

`format` / `format:check` is a **reserved auxiliary task name** (like `lint:fix`). It may exist
per-stack but is **never one of the 7 scored gates** — adding it would create per-stack unfairness
(Go's golangci-lint formatters block folds format into `lint`; Rust cargo aliases cannot chain
`fmt` into the `lint` alias — one subcommand only). Per-stack enforcement: Go `lint`
(golangci-lint) covers formatters; Next/Nest prettier via lint-staged; Django `ruff format --check`
as a poe task + pre-commit; Rust `cargo fmt --check` as a separate hook/CI step; Spring Boot
Spotless (google-java-format) via a hook/CI step, not folded into the scored `lint` task even
though Gradle could chain it — kept auxiliary for parity with every other stack. All enforced,
none an 8th gate.

## AGENTS.md contract snippet

This is the single source for the contract table adapters paste into scaffolded AGENTS.md
(adapters reference this file; they do not restate the table). Swap the command/runner columns
per stack.

```markdown
## Gates

These 7 names are the same in every guardrailed repo regardless of stack — an agent that has
never seen this repo can always discover and run gates via the runner or this table.

| Gate | Command | Runner file |
|---|---|---|
| lint | `npm run lint` | package.json |
| typecheck | `npm run typecheck` | package.json |
| test | `npm run test` | package.json |
| coverage | `npm run coverage` | package.json |
| build | `npm run build` | package.json |
| e2e | `npm run e2e` | package.json |
| audit | `npm run audit` | package.json |

Tests are mandatory — never ship code without them. Never lower a threshold, skip a test, or
pass a defang flag to make a gate green.
```

Per-stack column swaps: Django → `uv run poe <gate>` / `pyproject.toml`; Go → `just <gate>` /
`justfile`; Rust → `cargo <gate>` / `.cargo/config.toml`; Spring Boot → `./gradlew <gate>` /
`build.gradle`. Include one line naming the
load-bearing zero-warnings flag for the stack so a reviewer can spot a defanged gate.

## Anti-defang red flags

Each is one line; this doubles as a lint-the-lints checklist for future adapters.

- `--issues-exit-code=0` (golangci-lint) — swallows the failure.
- `--exit-zero` (ruff) — forces exit 0 regardless of findings.
- missing `-D warnings` (clippy) — warnings compile, gate never fails.
- missing `--max-warnings=0` (eslint) — warn-severity findings pass.
- `ignoreFailures = true` (Checkstyle/PMD/JaCoCo verification, Spring Boot) — swallows the failure.
- a meta-runner file duplicating a native one (a justfile in a JS or Rust repo) — two sources of truth.

## Unknown-repo probe order

For a repo not scaffolded by this skill (see `references/adapters/discovery.md`), detect the
runner in this order, stop at first match: `package.json` `scripts` → `build.gradle`(`.kts`)
`tasks.register(...)` names (+ Gradle's always-present native `test`/`build` tasks) →
`justfile` recipes → `Makefile` targets → `.cargo/config.toml` `[alias]` →
`pyproject.toml` `[tool.poe.tasks]` → README dev-section code blocks → infer per-stack native
defaults from the manifest present.
