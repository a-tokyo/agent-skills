# Adapter — Spring Boot (Gradle, Java 21)

Runner is **Gradle itself** — Java's most-native named-task mechanism, same principle that
keeps npm scripts and cargo aliases native (no justfile/poe needed here either). All 7 gate
names are literal Gradle tasks, invoked `./gradlew <gate>`. Config verified live this session
(`start.spring.io`, Maven Central, Gradle Plugin Portal, a real `./gradlew tasks` run) — see
`research/11-springboot-adapter.md` for full citations and the two live-verification catches
(an Initializr `bootVersion` bug, a stale-vs-live Maven coordinate for the audit plugin).

**Every version literal below is a generation-time snapshot** (Checkstyle, PMD, Error Prone,
dependency-check, Spotless, JaCoCo, Gradle wrapper, `java=21.0.11-tem`) — re-resolve each to its
current release via §6 before applying; do not transcribe these numbers as fixed facts.

## Contents

- Greenfield layout
- Initializr scaffold
- Checkstyle + PMD (lint, verbatim)
- Error Prone + strict javac (typecheck, verbatim)
- Test / e2e split + JaCoCo coverage (verbatim)
- build (native, documented divergence)
- audit (verbatim)
- Seed tests
- Toolchain + supply chain
- Hooks (verbatim)
- CI + Sonar

## Greenfield layout

```
{{APP}}/
├── build.gradle                                  # Groovy DSL — canon-owned, all 7 gates + plugins
├── settings.gradle
├── gradlew / gradlew.bat / gradle/wrapper/...     # committed wrapper (9.5.1) — framework-owned
├── gradle.lockfile                                # dependencyLocking output — commit this
├── .sdkmanrc                                      # exact local JDK/Gradle patch pin
├── config/checkstyle/checkstyle.xml
├── config/pmd/ruleset.xml
├── lefthook.yml
└── src/
    ├── main/java/com/example/{{app}}/
    │   ├── {{App}}Application.java                # bootstrap — excluded from coverage denominator
    │   ├── service/LengthClassifier.java           # seed: branchy util
    │   └── web/ClassifyController.java             # seed: branchy validated endpoint
    └── test/java/com/example/{{app}}/
        ├── {{App}}ApplicationTests.java            # Initializr's smoke test — keep
        ├── web/ClassifyControllerTest.java          # @WebMvcTest + MockMvc
        └── ClassifyE2eTest.java                     # @Tag("e2e"), real embedded server
```

## Initializr scaffold

```bash
curl -sS https://start.spring.io/starter.tgz \
  -d type=gradle-project -d language=java -d packaging=jar -d javaVersion=21 \
  -d groupId=com.example -d artifactId={{APP}} -d name={{APP}} \
  -d packageName=com.example.{{APP}} -d dependencies=web,validation \
  -o {{APP}}.tgz
mkdir {{APP}} && tar -xzf {{APP}}.tgz -C {{APP}} && rm {{APP}}.tgz && cd {{APP}} && rm HELP.md
```

**Never pass `bootVersion` explicitly** — a live-verified Initializr bug: an explicit
`bootVersion` (any value, incl. the metadata's own listed default) reproducibly 500s
("Bom ... could not be resolved"); omitting it and letting the server default resolve
succeeds. Verify the resolved version afterward in `build.gradle`'s
`org.springframework.boot` plugin line (session-verified: `4.1.0` GA) and report it — this is
the same posture as any other currency-ladder confirmation (SKILL.md §6), just with an
extra param-level landmine to avoid.

**Spring Boot 4.x renamed starters — do not use 3.x names from recall:**
`spring-boot-starter-web` → `spring-boot-starter-webmvc`; the universal
`spring-boot-starter-test` is now split per-technology
(`spring-boot-starter-webmvc-test`, `spring-boot-starter-validation-test`) — both already
wired by Initializr's generated `build.gradle`, no manual edit needed, but do not "fix" them
back to 3.x names. `TestRestTemplate` moved to
`org.springframework.boot.resttestclient.TestRestTemplate` and now needs an explicit
`@AutoConfigureTestRestTemplate` on the test class (see e2e below) — this did not exist before
Boot 4.0.

Java toolchain is auto-emitted (`languageVersion = JavaLanguageVersion.of(21)`). Before any
`./gradlew`/`java` invocation, ensure `JAVA_HOME` points at a **real JDK 21** on this machine —
**discover it, never hardcode a path** (a wrong path yields Gradle's confusing "JAVA_HOME is set to
an invalid directory"). Discovery ladder, first hit wins:

```bash
# 1. macOS: the canonical resolver
export JAVA_HOME="$(/usr/libexec/java_home -v 21)"
# 2. already-set env — verify it is a 21 JDK: java -version
# 3. sdkman: sdk use java 21.0.11-tem   (sets JAVA_HOME for the shell)
# 4. example only — Homebrew keg-only install on macOS:
#    export JAVA_HOME=/opt/homebrew/opt/openjdk@21
# Linux: distro path (e.g. /usr/lib/jvm/temurin-21) or the setup-java action in CI.
```

Prepend `$JAVA_HOME/bin` to `PATH` after resolving. If no JDK 21 is discoverable, offer the exact
install command; if the user declines, abort before Phase 1 (§6 — greenfield has no partial success):

```bash
# JDK 21 — sdkman (any platform, also sets JAVA_HOME): sdk install java 21.0.11-tem
#          macOS Homebrew: brew install openjdk@21   (keg-only — then use the ladder above)
#          Linux: install temurin-21 from Adoptium / your distro
# lefthook (hooks): brew install lefthook   (macOS)  |  else your package manager
```

## Checkstyle + PMD (lint, verbatim)

Two tools under one `lint` task — the same "curated broad set, one gate command" shape as
golangci-lint bundling many linters, or eslint spreading sonarjs+unicorn under one config.
**Not** SpotBugs (bytecode pass, overlaps what Error Prone already catches at compile time —
dropped for the same "don't run 3 static-analysis tools where every other stack runs ≤2"
reasoning `research/11 §2.1` states in full). PMD's `CognitiveComplexity` rule is the genuine
(non-degraded) S3776-parity axis — its own default `reportLevel` is already 15, stated
explicitly below anyway, matching every other adapter's practice of naming the threshold.

```gradle
checkstyle {
    toolVersion = '13.7.0'
    configDirectory = file('config/checkstyle')
    maxWarnings = 0
}
pmd {
    toolVersion = '7.26.0'
    ruleSetFiles = files('config/pmd/ruleset.xml')
    ruleSets = []
}
tasks.register('lint') {
    dependsOn 'checkstyleMain', 'checkstyleTest', 'pmdMain', 'pmdTest'
}
```

`config/pmd/ruleset.xml` references `category/java/bestpractices.xml`,
`category/java/errorprone.xml` (**PMD's own rule category name** — unrelated to the separate
Error Prone tool below; do not conflate the two when documenting this to a team), and
`category/java/design.xml` with the complexity threshold pinned:

```xml
<ruleset name="app-ai-guardrails" xmlns="http://pmd.sourceforge.net/ruleset/2.0.0.xsd">
    <rule ref="category/java/bestpractices.xml">
        <!-- Rationale-commented excludes: both predate AssertJ/MockMvc fluent assertions and
             do not recognize assertThat(...)/andExpect(...) chains as asserts (false positive
             on this seed's real test style). Current names since PMD 7.7.0; the old
             JUnit*-prefixed names are deprecated aliases that no longer resolve as excludes.
             NB: XML comments cannot contain a double hyphen. -->
        <exclude name="UnitTestShouldIncludeAssert"/>
        <exclude name="UnitTestContainsTooManyAsserts"/>
    </rule>
    <rule ref="category/java/errorprone.xml"/>
    <rule ref="category/java/design.xml">
        <exclude name="LawOfDemeter"/>  <!-- noisy on Spring DI constructor injection -->
        <exclude name="CognitiveComplexity"/>  <!-- re-included below with explicit threshold -->
    </rule>
    <rule ref="category/java/design.xml/CognitiveComplexity">
        <properties><property name="reportLevel" value="15"/></properties>
    </rule>
</ruleset>
```

(All of the above live-verified: this exact ruleset runs green on the seed under PMD 7.26.0.)

`checkstyle.xml` is a curated subset (import correctness/naming/whitespace hygiene), not the
full bundled `google_checks.xml` — same "curated, not `default: all`" precedent as Go's
golangci-lint list. **Do not include Checkstyle's `ImportOrder` module** — import *ordering* is
Spotless/google-java-format's job (the `format` auxiliary, D9); ImportOrder's opinion differs
from google-java-format's and the two fight (live-verified: gjf output fails ImportOrder).
Keep the content-correctness import checks (`AvoidStarImport`, `RedundantImport`,
`UnusedImports`) in Checkstyle. Zero-warnings: `maxWarnings = 0` (Checkstyle is
permissive-by-default like ESLint, needs the explicit flag) + both plugins' `ignoreFailures`
left at their strict-by-construction default `false` — a defang flag flipping this to `true`
is the red flag to grep for.

**Day-1 pre-fix (Initializr's own output fails the canon lint):** Initializr generates
tab-indented Java files, which fail Checkstyle `FileTabCharacter`. Run `./gradlew spotlessApply`
once after scaffolding (same class of generator pre-fix as Django's manage.py/ALLOWED_HOSTS
fixes) before expecting `lint` green.

## Error Prone + strict javac (typecheck, verbatim)

Java has no separate type-check pass distinct from compilation — `compileJava`/`compileTestJava`
**are** the type-checking pass. `typecheck` = those tasks run with every javac lint warning
promoted to an error, plus Error Prone (a **javac compiler plugin**, not a separate pass —
which is why it lives here, not on `lint`; semantically it plays `go vet`'s role: a
compile-time correctness check, not a style pass).

```gradle
plugins {
    id 'net.ltgt.errorprone' version '5.1.0'
}
dependencies {
    errorprone 'com.google.errorprone:error_prone_core:2.50.0'
}
tasks.withType(JavaCompile).configureEach {
    options.compilerArgs << '-Xlint:all' << '-Werror'
    options.errorprone {
        disableWarningsInGeneratedCode = true
    }
}
tasks.register('typecheck') {
    dependsOn 'compileJava', 'compileTestJava'
}
```

Divergence from Go's `vet`≠`build` split: Java's `typecheck` and `build` share the same
compiler underneath — the distinction is the **flag set** (`-Werror` + Error Prone vs.
`build`'s default lifecycle compile), not two different tools. State this plainly rather than
implying a tool split that doesn't exist.

## Test / e2e split + JaCoCo coverage (verbatim)

JUnit 5 `@Tag("e2e")` filtering on two `Test` tasks reading the same `test` sourceSet — the
least-machinery analog of Go's `//go:build e2e` file tag (no duplicated sourceSet/classpath).

```gradle
tasks.named('test') {
    useJUnitPlatform { excludeTags 'e2e' }
    finalizedBy 'jacocoTestReport'
}
tasks.register('e2e', Test) {
    useJUnitPlatform { includeTags 'e2e' }
    testClassesDirs = sourceSets.test.output.classesDirs
    classpath = sourceSets.test.runtimeClasspath
    shouldRunAfter 'test'
}

jacoco { toolVersion = '0.8.15' }
jacocoTestReport {
    reports { xml.required = true; html.required = true }
    afterEvaluate {
        classDirectories.setFrom(files(classDirectories.files.collect {
            fileTree(dir: it, exclude: ['**/*Application.class'])
        }))
    }
}
jacocoTestCoverageVerification {
    afterEvaluate {
        classDirectories.setFrom(files(classDirectories.files.collect {
            fileTree(dir: it, exclude: ['**/*Application.class'])
        }))
    }
    violationRules {
        rule {
            limit { counter = 'INSTRUCTION'; value = 'COVEREDRATIO'; minimum = 0.85 }
            limit { counter = 'BRANCH';      value = 'COVEREDRATIO'; minimum = 0.71 }
            limit { counter = 'METHOD';      value = 'COVEREDRATIO'; minimum = 0.76 }
            limit { counter = 'LINE';        value = 'COVEREDRATIO'; minimum = 0.86 }
        }
    }
}
tasks.register('coverage') {
    dependsOn 'test', 'jacocoTestReport', 'jacocoTestCoverageVerification'
}
```

JaCoCo genuinely has all 4 axes (unlike Go/Django's real degradations) — thresholds reuse the
N/Ne numbers verbatim (85/71/76/86), mapped `statements`→`INSTRUCTION` (the one honest proxy:
bytecode-instruction granularity, JaCoCo has no separate source-statement counter),
`branches`→`BRANCH`, `functions`→`METHOD`, `lines`→`LINE` (all three exact). `*Application.class`
excluded from both the report and verification denominator — the bootstrap-exclusion rule
(`references/canon/coverage.md`), same principle as Go's `-coverpkg`/Nest's `main.ts` exclusion.

## build (native, documented divergence)

**Left as Gradle's own native `build` lifecycle task, unmodified — deliberately not
redefined to match the other stacks' artifact-only `build` semantics.** Gradle's `build` is
natively `assemble + check` (compile, `test`, `checkstyleMain`/`pmdMain`, then the jar) — a
superset, by the ecosystem's own universal convention. (Live-verified: `check` does **not**
pull in `jacocoTestCoverageVerification` by default — coverage teeth live only on the
`coverage` gate; do not claim `build` enforces thresholds.) Fighting the convention
(`tasks.named('build') { setDependsOn(['assemble']) }`) would violate
every Java engineer's expectation of `./gradlew build` harder than the "use the most-native
mechanism" principle tolerates, and it costs nothing: a `build-green` failure is still a valid,
strictly *stronger* signal (any lint/test regression now also fails `build`), not a
weaker one. `assemble` alone remains available for anyone who wants artifact-only production.

## audit (verbatim)

OWASP dependency-check-gradle is the audit gate — **use the Gradle-Plugin-Portal coordinate
`org.owasp.dependencycheck`, not the legacy `org.owasp:dependency-check-gradle` Maven
coordinate** (a live cross-check this session found the latter mirrored stale at Maven
Central, frozen since 2024; the plugin-portal coordinate is live-current at `12.2.2`).

```gradle
plugins {
    id 'org.owasp.dependencycheck' version '12.2.2'
}
dependencyCheck {
    failBuildOnCVSS = 4.0
    nvd { apiKey = System.getenv('NVD_API_KEY') }
}
tasks.register('audit') { dependsOn 'dependencyCheckAnalyze' }
```

`failBuildOnCVSS = 4.0` fails closed on advisories ≥ moderate (CVSS 3.x MEDIUM starts at 4.0),
matching the cross-stack "audit fails ≥moderate" semantic. **Honest cost, name it in
AGENTS.md, do not hide it:** without an `NVD_API_KEY` (free, nvd.nist.gov), the first analysis
can take tens of minutes under NIST's anonymous rate limits — unlike Go/Rust's near-instant
audit on a fresh scaffold, Spring Boot's 40-80+ transitive jars mean "few deps" does not imply
"fast" here. Setup step (same class as D17's cargo-tool-install line): register a key, export
`NVD_API_KEY` locally and as a CI secret; CI additionally caches the local NVD data directory
across runs. The benchmark harness treats this exactly like e2e's browser-install caveat: run
live only with the key + network, else fall back to a config-present check.

## Seed tests

All four seeds below ran green in this session against Boot 4.1.0 / JDK 21 (full 7-gate run
minus audit, which is NVD-key-gated).

- `LengthClassifier` (`service/`) — 2-3 real branches (same shape as Nest's `classifyLength`),
  unit-tested to ~100%. Name the length cutoff as a `private static final int` constant — a
  bare literal in the `if` trips PMD's magic-number rule (live-verified).
- `ClassifyController` — `POST /classify`, `@Valid @NotBlank` request DTO: 400 on validation
  failure (Spring's default `MethodArgumentNotValidException` handling — no custom handler
  needed for the seed) vs. 200 with the classification. Tested via `@WebMvcTest` + `MockMvc`
  (fast, no server boot — parity with Go's `httptest.NewRecorder` tier). Boot 4 import:
  `org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest` (moved from 3.x's
  `...test.autoconfigure.web.servlet`). `@WebMvcTest` scans only the web slice — add
  `@Import(LengthClassifier.class)` or the service bean is missing and the context fails
  (live-verified failure mode).
- Keep Initializr's generated `{{App}}ApplicationTests.contextLoads()` — the smoke test.
- One `@Tag("e2e")` e2e test:

```java
@Tag("e2e")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureTestRestTemplate
class ClassifyE2eTest {
    @Autowired TestRestTemplate rest;
    // POST /classify over the real embedded Tomcat; assert 200 valid / 400 invalid
}
```

Real loopback HTTP against a real embedded server — same tier as Go's `httptest.NewServer` /
Rust's `oneshot` / Nest's supertest+`INestApplication`. Two Boot-4 landmines here, both
live-verified:

- `@AutoConfigureTestRestTemplate` (`org.springframework.boot.resttestclient.autoconfigure`)
  is new and mandatory in Boot 4 — in 3.x the template auto-configured implicitly.
- Add `testImplementation 'org.springframework.boot:spring-boot-starter-restclient'` —
  the TestRestTemplate auto-config needs `RestTemplateBuilder` from the separate
  `spring-boot-restclient` module, which `webmvc-test` does NOT pull in; without it the e2e
  context fails with `NoClassDefFoundError: o.s.boot.restclient.RestTemplateBuilder`.

## Toolchain + supply chain

```gradle
java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }
dependencyLocking { lockAllConfigurations() }
```

Gradle toolchains are **major-version-scoped by architecture** (any JDK matching 21, not an
exact patch, across vendors) — an honest ecosystem constraint, not a policy gap. For the
D-pattern exact-pin, add `.sdkmanrc` (SDKMAN's reproducible-env file, exact patch, local dev):

```
java=21.0.11-tem
gradle=9.5.1
```

`./gradlew dependencies --write-locks` writes `gradle.lockfile` (single file, project root) —
commit it, the Java analog of `package-lock.json`/`go.sum`/`Cargo.lock`. No native
min-release-age equivalent (honest negative, JS-only per D10).

## Hooks (verbatim)

`lefthook.yml` — reused verbatim in shape from Go/Rust (Java has the same no-`npm-prepare`
gap):

```yaml
pre-commit:
  parallel: true
  commands:
    format:
      glob: "*.java"
      run: ./gradlew spotlessApply
      stage_fixed: true
    lint:
      glob: "*.java"
      run: ./gradlew lint
pre-push:
  commands:
    test:
      run: ./gradlew test
```

`format` = Spotless + google-java-format (plugin `com.diffplug.spotless`, current `8.8.0`) —
**auxiliary, not the scored `lint` gate** (D9), enforced via this hook + CI, matching Rust's
`cargo fmt --check` staying a separate step rather than folding into the scored gate. Setup
docs and AGENTS.md name the one-liner: `lefthook install` (no auto-install, same honest
negative as Go/Rust).

## CI + Sonar

```yaml
- uses: actions/setup-java@<SHA>            # v5.4.0
  with: { distribution: temurin, java-version: '21' }
- uses: gradle/actions/wrapper-validation@<SHA>   # v6.2.0
- uses: gradle/actions/setup-gradle@<SHA>         # v6.2.0
- run: ./gradlew lint typecheck test coverage build e2e audit
```

`gradle/actions` (not the deprecated `gradle-build-action`) hosts both sub-actions in one repo.
`wrapper-validation` is Java-specific supply-chain surface no other stack needs — the committed
`gradle-wrapper.jar` is a binary (unlike npm/cargo/go's script-only wrappers); the action
checksums it against Gradle's known-good list, catching a tampered wrapper.

Sonar: the `org.sonarqube` Gradle plugin auto-infers `sonar.java.binaries`/`sonar.sources`/
`sonar.tests`/`sonar.junit.reportPaths` from the Gradle project model — genuinely lighter
wiring than every other stack's hand-written `sonar-project.properties`. Only the JaCoCo path
needs stating (and even that has a scanner-side default-location auto-detect):

```properties
sonar.coverage.jacoco.xmlReportPaths=build/reports/jacoco/test/jacocoTestReport.xml
```
