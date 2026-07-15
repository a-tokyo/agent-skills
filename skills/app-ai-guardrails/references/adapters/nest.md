# Adapter — NestJS

Shares the JS/TS canon shape with Next (same vitest v8, same `coverage-thresholds.mjs`, same
husky/lint-staged pattern). This file covers only what diverges. Templates carry no exact app-dep
versions except the one commented compat pin.

## Contents

- Scaffold
- tsconfig (key-merge)
- Vitest migration (replace Jest)
- ESLint (append)
- Coverage exclusions
- Seed tests
- Scripts / hooks / CI

## Scaffold

```bash
npx @nestjs/cli@latest new {{APP}} --strict --skip-git --package-manager npm
```

`--strict` enables per-flag strictness (**not** the `strict: true` umbrella — see tsconfig). Nest
always emits ESLint + Prettier (no opt-out flag). It generates jest config inline in `package.json`,
Nest-named scripts (`test:cov`, `test:e2e`…) that do **not** match the gate contract, and no
`typecheck` script — all fixed below.

## tsconfig (key-merge)

Nest's `--strict` gives `strictNullChecks` + `noImplicitAny` but not the umbrella. Set the umbrella,
then carve out one flag:

```jsonc
{
  "compilerOptions": {
    "strict": true,
    "strictPropertyInitialization": false, // DTO carve-out (D14): Nest DTOs/entities use bare property
                                            // declarations populated by class-validator/ORM at runtime,
                                            // not the constructor — the umbrella would break every one.
    "types": ["vitest/globals"]             // D13: inline, no separate tsconfig.test.json (Nest is single-project)
  }
}
```

Add one AGENTS.md rationale line for `strictPropertyInitialization: false` so it reads deliberate,
not defanged.

## Vitest migration (replace Jest)

Follow Nest's official first-party SWC recipe (verified working: DI unit spec and supertest e2e
both pass unmodified). Add devDeps `vitest`, `unplugin-swc`, `@swc/core`, `@vitest/coverage-v8`;
remove jest.

`vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import swc from "unplugin-swc";
import { COVERAGE_THRESHOLDS } from "./scripts/coverage-thresholds.mjs";

export default defineConfig({
  plugins: [swc.vite({ module: { type: "es6" } })],
  test: {
    globals: true,
    root: "./",
    esbuild: false,
    oxc: false, // Vitest 4 defaults transform to Oxc; both false silences the deprecation noise
    include: ["src/**/*.spec.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**"],
      exclude: ["src/main.ts", "src/**/*.module.ts", "src/**/*.spec.ts"],
      reporter: ["text", "lcov", "json-summary"],
      thresholds: COVERAGE_THRESHOLDS,
    },
  },
});
```

`vitest.config.e2e.ts` is the same shape with `include: ["test/**/*.e2e-spec.ts"]` and no coverage
block — the `e2e` gate runs `vitest run --config vitest.config.e2e.ts`.

`coverage-thresholds.mjs` is identical to Next's (same four keys — the sanctioned duplication of
`references/canon/coverage.md`):

```js
export const COVERAGE_THRESHOLDS = { statements: 90, branches: 85, functions: 90, lines: 90 };
```

## ESLint (append)

Nest's config is already `tseslint.config(...)`, so append one block (last-object-wins): spread
`sonarjs.configs.recommended.rules`, add the unicorn/unused-imports rules, disable
`sonarjs/no-unused-vars` and `sonarjs/void-use` (the latter lines up with Nest's own unawaited
`bootstrap()` idiom — keep `@typescript-eslint/no-floating-promises` at `warn`, Nest's default).
Swap `...globals.jest` → `...globals.vitest` (one-line key change). `lint` passes `--max-warnings=0`.
Pin `"eslint-plugin-unicorn": "^65"` with the same rationale comment as Next (eslint 9.x compat).
Reuse Next's rule details verbatim where they apply here: explicit options `{}` on any rule an
earlier spread configured (option-retention), the `**/*.d.ts` `no-var`/`vars-on-top` override,
`sonarjs/no-alphabetical-sort` off in the test-relaxation block (+ `e2e/**` in its globs), and
the "Org preset instead of inline config" section (the factory would be `nest({...})`).

## Coverage exclusions

Exclude `src/main.ts` (bootstrap, e2e-only) and `src/**/*.module.ts` (declarative wiring, no
branches) — otherwise the top-level line always looks like a failure. Matches
`references/canon/coverage.md`.

## Seed tests

- `src/**/classify-length.ts` — a branchy util (`classifyLength(v): 'empty'|'short'|'long'`, two
  `if` branches) + spec covering all three cases (drives branches 0→100%, flips the gate 1→0).
- Keep the generated `app.controller.spec.ts` (Nest's `Test.createTestingModule` + `.compile()` DI
  pattern) — it runs unmodified under vitest and is the idiomatic controller unit-test seed.
- Keep the generated `test/app.e2e-spec.ts` (supertest against a real `INestApplication`) as the
  `e2e` seed — runs unmodified under `vitest.config.e2e.ts`, just renamed to the `e2e` gate.

**D15:** `nest generate` emits jest-style specs (describe/it/expect) — accepted for v1; they run
under vitest globals. No custom spec templates; agents copy the seed vitest pattern for new specs.

## Scripts / hooks / CI

Scripts map to the 7 gate names (`typecheck` = `tsc --noEmit -p tsconfig.json`; `test` =
`vitest run`; `coverage` = `vitest run --coverage`; `e2e` = `vitest run --config vitest.config.e2e.ts`;
`build` = `nest build`; `audit` = `node scripts/audit-gate.mjs`; plus `format`, `prepare: husky`).
Hooks (husky trio + lint-staged), `audit-gate.mjs`, `.npmrc`, `.nvmrc`, and the CI job reuse the
Next paths unchanged (`sonar.javascript.lcov.reportPaths=coverage/lcov.info`).
