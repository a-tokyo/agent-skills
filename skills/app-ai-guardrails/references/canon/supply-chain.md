# Supply chain

## Audit map

The `audit` gate maps to each stack's scanner and fails closed on advisories ≥ moderate.
**A real finding is always code-red** (remediate: upgrade, override, or remove — never excuse it
as environmental); the environmental-red exit (SKILL §6) applies only when the scanner *could not
run or reach its advisory data* (offline DB, blocked mirror) — what a scan FOUND is never
environmental.

| Stack | Scanner | Notes |
|---|---|---|
| Next / Nest | `npm audit` via a `scripts/audit-gate.mjs` wrapper | fail ≥ moderate; empty allowlist array documented at the top |
| Django | `uv audit` | native (OSV); **experimental** at uv 0.11.1 — document the caveat; `pip-audit` is the fallback |
| Go | `govulncheck ./...` | reachability-aware (reports only vulns whose symbols are actually called) |
| Rust | `cargo deny check` | supersedes cargo-audit: advisories + licenses + bans + sources in one `deny.toml` |
| Spring Boot | OWASP dependency-check-gradle (`org.owasp.dependencycheck`) | fail closed via `failBuildOnCVSS=4.0`; NVD API key strongly recommended (else NIST rate limits make a first run slow) — honest cost, not hidden |

## Lockfiles

Commit the lockfile; verify it in CI with the frozen flag.

| Stack | Lockfile | CI flag |
|---|---|---|
| Next / Nest | `package-lock.json` | `npm ci` |
| Django | `uv.lock` | `uv sync --locked` (+ `uv run --frozen` per gate) |
| Go | `go.sum` | auto-verified (GOSUMDB on) |
| Rust | `Cargo.lock` (committed unconditionally for a `--bin` crate) | `--locked` |
| Spring Boot | `gradle.lockfile` (`dependencyLocking { lockAllConfigurations() }`) | `./gradlew dependencies --write-locks` to (re)generate |

## Toolchain-pin policy

**Always pin the toolchain; never pin app deps** (the lockfile pins dep reality). Templates carry
no exact app-dep versions.

| Stack | Toolchain pin |
|---|---|
| Next / Nest | `.nvmrc` (Node ≤24 keeps corepack) + `engines.node` |
| Django | `.python-version` (from `uv init`) |
| Go | `go.mod` `go` **and** `toolchain` directives at the same version |
| Rust | `rust-toolchain.toml` exact `channel = "1.x.y"` (not `"stable"`) + `components` |
| Spring Boot | Gradle `java.toolchain.languageVersion` (major-version-scoped, architecturally not exact-patch) + `.sdkmanrc` (`java=21.0.11-tem`) for exact local patch pin |

## min-release-age

JS-only. `.npmrc` `min-release-age=7` (days) for npm; pnpm uses `pnpm-workspace.yaml`
`minimumReleaseAge` (minutes) — `.npmrc` is auth-only since pnpm 11.

**Honest negatives:** Django, Go, Rust, and Spring Boot have **no native min-release-age
equivalent** (uv/coverage.py: none; Go: none, `gomod-age` is third-party; Rust: RFC 3923
proposed, unimplemented; Gradle: no native concept). Do not fabricate one. This is a
documented JS bonus, not a rubric probe.

## Java-only: wrapper-jar integrity

Spring Boot is the only stack that commits a **binary** as part of its own build tooling
(`gradle/wrapper/gradle-wrapper.jar`) rather than a script-only wrapper. CI runs
`gradle/actions/wrapper-validation` before anything else — it checksums the committed jar
against Gradle's own known-good list, catching a tampered wrapper. No other stack has this
surface (npm/cargo/go wrappers are plain scripts or absent).

## Go don'ts

Never disable checksum verification to unblock a build: no `GOSUMDB=off`, no `GONOSUMCHECK`, no
`GOFLAGS=-insecure`. Keep `GOPROXY=https://proxy.golang.org,direct` unless the org runs a private
proxy, and never pair a private proxy with `GOSUMDB=off`.
