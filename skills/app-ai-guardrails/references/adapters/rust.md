# Adapter — Rust (axum HTTP service)

Runner is **cargo aliases** (`.cargo/config.toml [alias]`) — each of the 7 gates is a single cargo
subcommand, so aliases satisfy the contract 1:1 with no justfile layered on top.

## Contents

- Greenfield recipe
- rust-toolchain.toml
- Static analysis (lints + clippy.toml)
- cargo aliases (verbatim)
- Coverage
- Seed tests
- deny.toml (verbatim)
- Hooks + tool prerequisite
- CI

## Greenfield recipe

```bash
cargo new {{APP}} --bin --vcs none    # --vcs none so it doesn't nest its own git repo
cargo add axum tokio --features tokio/full
```

Then build a minimal axum service (a `Router` with one branchy handler). axum keeps every stack a
web service (comparable benchmark shape) and `tower::ServiceExt::oneshot` lets integration tests
drive the real `Router` in-process — no socket, no flaky "connection refused."

## rust-toolchain.toml

Pin an **exact** version, never `channel = "stable"` (stable drifts a clippy lint into CI without
failing locally). `components` here makes rustup auto-install clippy/rustfmt.

`1.96.0` below is a **snapshot example** — resolve the current stable at generation time (§6, "version
literals are snapshots") and pin that; do not ship an ancient-but-resolvable channel that passes green
while silently freezing an outdated toolchain.

```toml
[toolchain]
channel = "1.96.0"   # snapshot — pin the current stable resolved at generation time
components = ["clippy", "rustfmt"]
```

## Static analysis

`[lints]` in `Cargo.toml`. `priority = -1` is required when a lint group is combined with individual
overrides so the specific override outranks the group. `pedantic`/`nursery` are **warn, never deny**
(nursery lints are pre-stabilization and churn across releases). `cognitive_complexity` moved to
`restriction` in Aug 2025 — it must be opted into per-lint (never enable `restriction` as a group).

```toml
[lints.rust]
warnings = "deny"

[lints.clippy]
all      = { level = "warn", priority = -1 }
pedantic = { level = "warn", priority = -1 }
nursery  = { level = "warn", priority = -1 }
cognitive_complexity = "warn"   # restriction lint, explicit opt-in — S3776 parity
```

`clippy.toml` (repo root, sibling to `Cargo.toml`):

```toml
cognitive-complexity-threshold = 15   # matches sonarjs S3776 (default is 25)
```

The `lint` alias carries `-D warnings` (belt-and-suspenders with the declarative
`[lints.rust] warnings = "deny"`).

## cargo aliases (verbatim)

`.cargo/config.toml`:

```toml
[alias]
lint      = "clippy --all-targets --all-features -- -D warnings"
typecheck = "check --all-targets"
test      = "test --workspace --lib --bins"
coverage  = "llvm-cov --workspace --lcov --output-path lcov.info --fail-under-lines 86 --fail-under-functions 76 --fail-under-regions 71"
e2e       = "test --workspace --test e2e"
build     = "build --release"
audit     = "deny check"
```

`test` uses `--lib --bins` so unit tests don't double-run the e2e integration test; `e2e` targets
`tests/e2e.rs` via `--test e2e`. `typecheck` = `cargo check` (compiles through type-checking, no
codegen — the `tsc --noEmit` analog). `cargo fmt --check` is a **separate hook/CI step, never
chained into an alias** (aliases are single-subcommand — D9).

## Coverage

cargo-llvm-cov, three stable axes (lines/functions/regions). **regions** stands in for both
statements and branches (branch coverage is nightly-only, unstable — not used), using the lower TS
floor (71). `main.rs` is the thin binary excluded; lib/handlers are the covered surface
(`references/canon/coverage.md`).

## Seed tests

- A branchy axum handler (validation → 400 vs 200) with inline `#[cfg(test)] mod tests` unit tests
  (`use super::*;` for private access).
- `tests/e2e.rs` — an integration test building the real `Router` and driving it via
  `tower::ServiceExt::oneshot` (full middleware/routing/extractor stack, in-process). A real
  bound-socket + `reqwest` test is the escalation, not the default.

## deny.toml (verbatim)

```toml
[advisories]
ignore = []          # empty allowlist, documented

[licenses]
allow = ["MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "Unicode-3.0"]

[bans]
multiple-versions = "warn"   # don't hard-fail on diamond duplicates in a fresh scaffold

[sources]
unknown-registry = "deny"
unknown-git = "deny"
```

`cargo deny check` (the `audit` gate) supersedes cargo-audit: advisories + licenses + bans + sources
in one step. `Cargo.lock` committed unconditionally (binary crate). No native min-release-age (RFC
3923 proposed, unimplemented — honest negative).

## Hooks + tool prerequisite

`lefthook.yml`: `pre-commit` runs `cargo fmt --check` on staged `*.rs`; `pre-push` runs
`cargo clippy --all-targets -- -D warnings` (clippy can't be file-scoped — it compiles the whole
crate). Like Go, no `prepare` equivalent — document `lefthook install` (run once).

**Tool prerequisite (D17):** `coverage` and `audit` need cargo-llvm-cov and cargo-deny on PATH;
hooks need `lefthook`. Probe in Phase 0; if missing, offer the exact commands, and if the user
declines, abort before Phase 1 (§6 — greenfield has no partial success). AGENTS.md names them:

```bash
cargo install cargo-llvm-cov cargo-deny --locked
brew install lefthook   # macOS; else: cargo install lefthook (any platform)
```

Same class as golangci-lint/just for Go.

## CI

`dtolnay/rust-toolchain` → `Swatinem/rust-cache@v2` (after the toolchain step; its key derives from
the active rustc) → `taiki-e/install-action` (SHA-pinned) for cargo-llvm-cov + cargo-deny. Rust is
first-party on SonarCloud since 2025: `sonar.rust.lcov.reportPaths=lcov.info` +
`sonar.rust.clippy.enabled=true`.
