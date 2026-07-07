# CI + SonarCloud

## Contents

- Universal job DAG
- Hardening + SHA-pin policy
- Runner choice
- Per-stack setup-steps
- SonarCloud wiring
- Human checklist
- Workflow skeleton (verbatim template)
- sonar-project.properties skeletons (per stack)

## Universal job DAG

Jobs: `{ static/lint · build · unit · e2e · gate · sonar }`. `gate` needs the upstream jobs and
fails if any failed. `sonar` runs parallel to `gate`, gated on the repo var so CI is green day-1
without an org. The workflow — not any task-runner — does the sequencing; each job invokes gate
names via the stack runner.

## Hardening + SHA-pin policy

- `permissions: contents: read` at the top (least privilege).
- A `concurrency` block keyed on event + ref (cancel superseded runs).
- **Every `uses:` is a full 40-char commit SHA.** Resolve tags to SHAs with `gh api` in Phase 5.
  Degradation: `gh` unreachable → WebFetch the repo's commit page → if still unresolved, keep the
  tag ref and append `# TODO(pin): <tag>`, listed in the Phase 7 report. A tag ref without the
  TODO comment is a defect.

## Runner choice

A single `runs-on` decision at Phase 0, no matrix/conditional runner switching.

- `ubuntu-latest` — default, zero setup.
- `ubicloud-standard-2` — ~80% cheaper (5x) than GitHub-hosted; requires an **org-level GitHub-App
  install** (a human step, see checklist). The label still works after Ubicloud's June-2026 move to
  premium hardware (activation is account-level, not a new label).

## Per-stack setup-steps

| Stack | Setup steps (before the gate invocations) |
|---|---|
| Next / Nest | `actions/setup-node` + `npm ci` |
| Django | `astral-sh/setup-uv` (replaces setup-python) → `uv sync --locked` → `uv run --frozen poe <gate>` |
| Go | `actions/setup-go@v6` + `extractions/setup-just@v4` (just is NOT preinstalled) + `golangci/golangci-lint-action@v9` + a `govulncheck` step |
| Rust | `dtolnay/rust-toolchain` → `Swatinem/rust-cache@v2` (after toolchain) → `taiki-e/install-action` (cargo-llvm-cov, cargo-deny) |
| Spring Boot | `actions/setup-java` (temurin, 21) → `gradle/actions/wrapper-validation` (checksums the committed `gradle-wrapper.jar`) → `gradle/actions/setup-gradle` (caching) |

## SonarCloud wiring

- Scan action: `SonarSource/sonarqube-scan-action` — resolve the latest major via the Phase 0
  currency ladder, then pin to its SHA. Do **not** copy version tags from old snippets.
- Run the scan **without** `-Dsonar.qualitygate.wait=true` (Sonar's docs recommend against it on
  PRs — it inflates workflow duration), then add a separate
  `SonarSource/sonarqube-quality-gate-action` step to block on the result.
- Gate the whole sonar job on `vars.SONAR_ENABLED == 'true'` so CI is green before an org exists.
- Per-language coverage property. The scan reuses the SAME report file the `coverage` gate
  produced — never a second coverage run. When the sonar job is separate from the job that ran
  `coverage` (the skeleton above), pass the report between jobs with
  `actions/upload-artifact`/`download-artifact`; same-job scans just read the file:

| Language | Property | Format |
|---|---|---|
| JS/TS | `sonar.javascript.lcov.reportPaths` | LCOV (covers TS too; `sonar.typescript.*` is deprecated) |
| Python | `sonar.python.coverage.reportPaths` | Cobertura XML (from `pytest-cov --cov-report=xml`) |
| Go | `sonar.go.coverage.reportPaths` | native `go test -coverprofile` file (no conversion) |
| Rust | `sonar.rust.lcov.reportPaths` | LCOV (from `cargo-llvm-cov --lcov`); Rust analysis is first-party (no community plugin needed) |
| Spring Boot | `sonar.coverage.jacoco.xmlReportPaths` | JaCoCo XML; the `org.sonarqube` Gradle plugin auto-infers `sonar.java.binaries`/`sources`/`tests`/`junit.reportPaths` from the Gradle model — lighter than every other stack |

## Human checklist (emit in the Phase 7 report)

- Create the SonarCloud project; **disable Automatic Analysis** (conflicts with CI analysis).
- Set New Code = reference branch.
- Add the `SONAR_TOKEN` secret; set repo var `SONAR_ENABLED=true`.
- Mark the quality gate a required branch-protection check (alongside `gate`).
- If using Ubicloud: install the Ubicloud Managed Runners GitHub App at the org (the skill cannot
  do this).

## Workflow skeleton (verbatim template)

Adapt only the named slots (`{{APP}}`, the per-stack setup block, the runner label). Ship SHAs,
not tags, after Phase 5.

```yaml
name: ci
on:
  push: { branches: [main] }
  pull_request:
permissions:
  contents: read
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  gate:
    runs-on: ubuntu-latest   # or ubicloud-standard-2
    steps:
      - uses: actions/checkout@<SHA>  # TODO(pin): v5
      # --- per-stack setup block (see table above) ---
      - run: <runner> lint
      - run: <runner> typecheck
      - run: <runner> test
      - run: <runner> coverage
      - run: <runner> build
      - run: <runner> e2e
      - run: <runner> audit
  sonar:
    needs: [gate]
    if: vars.SONAR_ENABLED == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<SHA>  # TODO(pin): v5
      - uses: SonarSource/sonarqube-scan-action@<SHA>  # TODO(pin): v8
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
      - uses: SonarSource/sonarqube-quality-gate-action@<SHA>  # TODO(pin): v1
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

## sonar-project.properties skeletons

```properties
# Next / Nest (src-layout)
sonar.projectKey=<org>_<repo>
sonar.organization=<org>
sonar.sources=src
sonar.tests=src
sonar.test.inclusions=**/*.test.ts,**/*.spec.ts
sonar.javascript.lcov.reportPaths=coverage/lcov.info
sonar.sourceEncoding=UTF-8
```

```properties
# Django
sonar.projectKey=<org>_<repo>
sonar.organization=<org>
sonar.sources=.
sonar.exclusions=**/migrations/**,**/venv/**,**/__pycache__/**,manage.py
sonar.python.coverage.reportPaths=coverage.xml
sonar.sourceEncoding=UTF-8
```

```properties
# Go
sonar.projectKey=<org>_<repo>
sonar.organization=<org>
sonar.sources=.
sonar.exclusions=**/*_test.go,**/testdata/**
sonar.go.coverage.reportPaths=coverage.out
sonar.sourceEncoding=UTF-8
```

```properties
# Rust
sonar.projectKey=<org>_<repo>
sonar.organization=<org>
sonar.sources=src
sonar.rust.clippy.enabled=true
sonar.rust.lcov.reportPaths=lcov.info
sonar.sourceEncoding=UTF-8
```

```properties
# Spring Boot — org.sonarqube Gradle plugin auto-infers sonar.java.binaries/sources/tests/
# junit.reportPaths from the Gradle model; only the JaCoCo path needs stating explicitly.
sonar.projectKey=<org>_<repo>
sonar.organization=<org>
sonar.coverage.jacoco.xmlReportPaths=build/reports/jacoco/test/jacocoTestReport.xml
sonar.sourceEncoding=UTF-8
```
