# Coverage — thresholds with teeth

## Teeth principle

The `coverage` gate **enforces** — it exits non-zero when any axis falls below its floor. It is
never report-only. This is the single largest behavioral guardrail after the gate contract
itself. A threshold with teeth plus a seed that clears it means the repo starts strict and green
on commit #1, with no "tighten later" debt.

## Per-stack thresholds (doc source of truth)

These numbers live here and in each adapter's literal config block — the only sanctioned
duplication (the config block *is* the artifact). They do not appear in SKILL.md.

| Stack | Tool | Axes | Threshold(s) |
|---|---|---|---|
| Next / Nest | vitest v8 | statements, branches, functions, lines | **statements 90 · branches 85 · functions 90 · lines 90** |
| Django | coverage.py (`branch=true`) | statements + branches (blended); no functions metric | **single `--cov-fail-under=90`** |
| Go | `go tool cover` | statements only | **single statements-total 90** over app packages |
| Rust | cargo-llvm-cov | lines, functions, regions (no stable branch) | **lines 90 · functions 90 · regions 85** |
| Spring Boot | JaCoCo | instruction, branch, method, line (all 4 native — least-degraded non-JS stack) | **instruction 90 · branch 85 · method 90 · line 90** |

## Denominator rule

Every stack excludes its bootstrap/wiring from the denominator — the uniform principle that
keeps the numbers honest (not green-by-excluding-everything). Exclude only bootstrap, never real
logic.

| Stack | Excluded from the coverage denominator |
|---|---|
| Next | tests, config, generated; page-smoke uses a `next/font` mock |
| Nest | tests, config; `main.ts` + `*.module.ts` (bootstrap + declarative wiring) |
| Django | `*/migrations/*`, `manage.py`, `config/asgi.py`, `config/wsgi.py`, `*/tests*` |
| Go | thin `cmd/*/main.go` entrypoint, via `-coverpkg=./internal/...` scope |
| Rust | `main.rs` thin binary; lib/handlers are the covered surface |
| Spring Boot | `*Application.class` (bootstrap), via `jacocoTestReport`/`jacocoTestCoverageVerification` classDirectories filter |

## Seed principle

Concrete seed shapes are stack-specific and live in each adapter (co-located, no shared
seed-tests file). The shared principle, everywhere:

- One **branchy pure util** with real decision points, tested to ~100% — prevents 0/0-branch
  fragility where a branchless file hits 100% trivially and the threshold means nothing.
- One **branchy endpoint/handler** test (validation → different responses for valid vs invalid).
- One **smoke** test (the app boots / a page renders).
- **One e2e** test.
- Wiring is excluded from the denominator (above), so the seed's numbers are honestly green.

## Never lower

Never lower a threshold to make the gate green. A red coverage gate is fixed by adding tests,
never by dropping the floor. This is a hard invariant (SKILL.md §3).

## Honest degradation

Coverage axes are a language-toolchain property, not a policy knob — the stacks do **not**
enforce identical dimensions, and the skill must not pretend they do:

- **Go** ships one number (statements). Its cover tool has no branch/function/line-distinct mode
  — never fabricate a branches number or approximate it via complexity. `-covermode=atomic`
  because the seed HTTP server is concurrent.
- **Django** has statements + branches (coverage.py `branch=true`) but no functions metric — one
  blended `--cov-fail-under`, not four axes.
- **Rust** has lines/functions/regions on stable; branch coverage is nightly-only and unstable,
  so it is not used. **regions** stands in as the closest stable proxy for both statements and
  branches, using the lower TS floor (71).
- **Spring Boot** is the exception that needs no degradation: JaCoCo natively counts
  instruction/branch/method/line, so all four N/Ne floors carry over unchanged — the only proxy
  is `statements`→`INSTRUCTION` (bytecode-instruction granularity; JaCoCo has no separate
  source-statement counter), branch/method/line map exactly.

State the stack's real axis count plainly; do not invent missing axes.
