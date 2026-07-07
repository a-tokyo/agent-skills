# Adapter — Go (net/http HTTP service)

Runner is a **justfile** — Go has no native named-task mechanism and no dep-graph tool to carry a
task runner, so it is the one stack where a secondary file is unavoidable. Config recipes below are
from current docs (no Go toolchain in the research environment) — flag any recipe as verify-on-first-run.

**Compatibility floor:** the `coverage` gate runs a POSIX `sh`+`awk` script and the justfile recipes
assume a POSIX shell. On **Windows this adapter requires Git-Bash or WSL** — native PowerShell/cmd
cannot run the coverage gate. State this floor honestly at Phase 0 (an honest early stop beats a
Phase-6 death spiral); if no POSIX shell is available, treat it as a declined-toolchain abort (§6).

## Contents

- Greenfield layout
- golangci-lint (verbatim)
- justfile (verbatim)
- Coverage gate script (verbatim)
- Seed tests
- Supply chain
- Hooks (verbatim)
- CI

## Greenfield layout

Go has no scaffolder — write files directly:

```
{{APP}}/
├── go.mod                 # go + toolchain directives, same version
├── cmd/{{APP}}/main.go    # thin entrypoint
└── internal/             # handlers, server wiring (unexported app code)
```

```bash
go mod init <module-path>
```

`go.mod` pins both `go 1.2X.Y` (minimum) and `toolchain go1.2X.Y` (exact) at the same current stable
release — Go's `.nvmrc`/`rust-toolchain.toml` equivalent. Skip `pkg/`/`api/`/etc.; add dirs as the
app grows.

## Tool prerequisites (Phase 0 preflight)

`golangci-lint`, `just`, and `lefthook` must be on PATH — none auto-install. Probe each in Phase 0;
if missing, offer the exact install command, and if the user declines, abort before Phase 1 (§6 —
greenfield has no partial success). Exact commands:

```bash
# macOS (Homebrew):
brew install golangci-lint just lefthook
# Linux / no Homebrew (go install works for all three):
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
go install github.com/casey/just@latest        # or: cargo install just
go install github.com/evilmartians/lefthook@latest
```

Same class as Rust's `cargo install` prerequisite line — name it in AGENTS.md setup.

## golangci-lint (verbatim)

`.golangci.yml` — v2 schema (v1 config is invalid under v2), curated-broad set (not `default: all`,
which free-rides on golangci's own selection and drifts). `gocognit` is the direct S3776 parity
linter (cognitive, not gocyclo's cyclomatic) at `min-complexity: 15`.

```yaml
version: "2"
linters:
  enable:
    - errcheck
    - govet
    - staticcheck
    - gosec
    - revive
    - gocognit
    - sqlclosecheck
    - bodyclose
    - unused
    - ineffassign
    - unconvert
    - unparam
    - nilerr
    - errorlint
    - contextcheck
    - noctx
    - exhaustive
  settings:
    gocognit:
      min-complexity: 15
formatters:
  enable:
    - gofmt
    - goimports
```

Zero-warnings is by construction — `golangci-lint run` exits non-zero on any issue; never pass
`--issues-exit-code=0`.

## justfile (verbatim)

`typecheck` = `go vet ./...` is deliberately **distinct** from `build` = `go build ./...` — vet is a
check-only pass (the semantic analog of `tsc --noEmit`), build produces the artifact.

```just
lint:
    golangci-lint run

typecheck:
    go vet ./...

test:
    go test -covermode=atomic ./...

coverage:
    go test -covermode=atomic -coverpkg=./internal/... -coverprofile=coverage.out ./...
    ./scripts/coverage-gate.sh coverage.out 80

build:
    go build ./...

e2e:
    go test -covermode=atomic -tags e2e ./...

audit:
    govulncheck ./...
```

`-covermode=atomic` because the seed HTTP server is concurrent by construction. `-coverpkg=./internal/...`
scopes coverage to app packages, excluding the thin `main.go` (the denominator rule,
`references/canon/coverage.md`).

## Coverage gate script (verbatim)

Go has no native fail-under. Self-owned script (stdlib-first, no third-party dependency),
`scripts/coverage-gate.sh`:

```sh
#!/usr/bin/env sh
set -eu
profile="${1:-coverage.out}"
floor="${2:-80}"
total=$(go tool cover -func="$profile" | awk '/^total:/ {gsub(/%/,"",$3); print $3}')
awk -v t="$total" -v f="$floor" 'BEGIN { exit !(t+0 >= f+0) }' \
  || { echo "coverage ${total}% below floor ${floor}%"; exit 1; }
echo "coverage ${total}% >= ${floor}%"
```

Go ships one number (statements only) — never fabricate a branches axis (`references/canon/coverage.md`).

## Seed tests

- A handler in `internal/` with a real branch (e.g. `/echo` returns 400 on a missing query param,
  200 otherwise) — a branchless handler hits 100% trivially and teaches nothing.
- A table-driven `_test.go` using `httptest.NewRequest` + `httptest.NewRecorder()` calling the
  handler directly (fast unit layer, exercises errcheck on `w.Write`).
- One `httptest.NewServer`-based test in a file tagged `//go:build e2e` (so it is excluded from the
  `test` and `coverage` runs and only the `e2e` recipe's `-tags e2e` picks it up), exercising the
  full `net/http` stack over a real loopback connection.

## Supply chain

`govulncheck ./...` (reachability-aware) is the `audit` gate. `go.sum` is auto-verified (GOSUMDB
on) — never disable it (`references/canon/supply-chain.md` Go don'ts). No native min-release-age
(honest negative; `gomod-age` is a v0.2 candidate, not adopted).

## Hooks (verbatim)

`lefthook.yml` (a single Go binary, no runtime dependency):

```yaml
pre-commit:
  parallel: true
  commands:
    format:
      glob: "*.go"
      run: gofmt -l {staged_files}
    lint:
      glob: "*.go"
      run: golangci-lint run --fix
      stage_fixed: true
pre-push:
  commands:
    test:
      run: go test -covermode=atomic -race ./...
```

Go has **no `npm prepare` equivalent** — hook install is a documented one-liner, not automatic
(honest negative). Setup docs and AGENTS.md name: `lefthook install` (run once).

## CI

`actions/setup-go@v6` + `extractions/setup-just@v4` (just is NOT preinstalled on GitHub-hosted
runners) + `golangci/golangci-lint-action@v9` + a `govulncheck` step. Sonar property
`sonar.go.coverage.reportPaths=coverage.out` (native format, no conversion — the same file the
`coverage` recipe produces).
