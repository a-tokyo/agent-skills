# Adapter — Next.js (App Router, TypeScript)

The deep reference. Templates carry no exact app-dep versions (the lockfile pins reality); the one
exception is a compatibility-mandated range pin with a rationale comment.

## Contents

- Scaffold (create-next-app)
- Package-manager divergence
- Day-1 pre-fixes (both live-verified)
- ESLint (replace)
- tsconfig (key-merge)
- Vitest config (verbatim)
- Coverage thresholds module
- Seed tests
- Playwright e2e
- Scripts (key-merge)
- Hooks (verbatim)
- Supply chain + CI delta
- AGENTS.md

## Scaffold

Pass the full explicit flag set **and** `--yes` — `--yes` fills only unspecified options, explicit
flags win, and together they guarantee a prompt-free run even if CNA defaults drift:

```bash
npx create-next-app@latest {{APP}} \
  --yes --ts --app --tailwind --eslint --src-dir --import-alias "@/*" --use-npm --disable-git
```

- Always pass `--eslint` explicitly (never rely on the undocumented default; never let `--biome`
  slip in — the whole sonar-merge strategy needs the flat `eslint.config.mjs` that `--eslint` emits).
- **Drop `--turbopack`** — Turbopack is the stable default in Next 16; the flag is a silent no-op and
  is not in `--help`.
- CNA generates `AGENTS.md` (default-on) + a plain `CLAUDE.md` containing `@AGENTS.md`. Keep both.

Template class per artifact: **replace** `eslint.config.mjs`; **key-merge** `package.json`,
`tsconfig.json`, `AGENTS.md`; everything else is a fresh **verbatim** add.

## Package-manager divergence

npm is THE path. This table covers the locked scope — no parallel install paths.

| | npm (default) | pnpm |
|---|---|---|
| min-release-age | `.npmrc` `min-release-age=7` (days) | `pnpm-workspace.yaml` `minimumReleaseAge` (minutes); `.npmrc` is auth-only since pnpm 11 |
| CI install | `npm ci` | `pnpm install --frozen-lockfile` |

corepack is bundled in Node ≤24 and removed in Node 25+ — the `.nvmrc` pin (below) keeps it free;
a future bump to Node 25+ needs an explicit corepack install step.

If Phase 0 selected **yarn (berry/PnP)** or **bun**, the only divergence beyond CI install
(`yarn install --immutable` / `bun install --frozen-lockfile`) is the pre-commit hook invocation —
PnP has no `node_modules/.bin`, so use the launcher-safe forms in the Hooks section, never a `.bin`
path.

## Day-1 pre-fixes (both live-verified failures)

CNA ships only `layout.tsx` (imports `next/font/google`) + `page.tsx` — no lib util, no client
component. The seed adds those; two fixes are **mandatory**, not optional:

1. `@vitejs/plugin-react` does **not** read `tsconfig.json` `paths`. The `@/*` alias must be
   re-declared in `vitest.config.ts` `resolve.alias`, or every `@/...` import fails to resolve.
2. `next/font/google` cannot execute outside Next's build pipeline. Any test that imports
   `layout.tsx` must `vi.mock("next/font/google", ...)` first — this is exactly the job of the
   page-smoke setup file.

## ESLint (replace)

Replace CNA's `eslint.config.mjs`. CNA's output is `defineConfig([...nextVitals, ...nextTs,
globalIgnores([...])])`; the merge is a pure append — insert `eslint-config-prettier/flat` as a
third base spread, then add the plugins block, then extend the ignores.

```js
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettierFlat from "eslint-config-prettier/flat";
import sonarjs from "eslint-plugin-sonarjs";
import unicorn from "eslint-plugin-unicorn";
import unusedImports from "eslint-plugin-unused-imports";
import tsdoc from "eslint-plugin-tsdoc";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  ...prettierFlat,
  {
    plugins: { sonarjs, unicorn, "unused-imports": unusedImports, tsdoc },
    rules: {
      ...sonarjs.configs.recommended.rules,
      "sonarjs/cognitive-complexity": ["error", 15],
      "sonarjs/no-unused-vars": "off",       // superseded by unused-imports
      "@typescript-eslint/no-unused-vars": "off",
      "unused-imports/no-unused-imports": "error",
      "unused-imports/no-unused-vars": ["error", { vars: "all", varsIgnorePattern: "^_", argsIgnorePattern: "^_" }],
      "tsdoc/syntax": "error",
      "no-console": "error",
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}", "tests/**", "**/*.config.*"],
    rules: { "sonarjs/no-hardcoded-passwords": "off", "sonarjs/pseudo-random": "off", "no-console": "off" },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "coverage/**"]),
]);
```

`eslint-plugin-unicorn` is pinned in `package.json` `devDependencies` as
`"eslint-plugin-unicorn": "^65"` **with this rationale comment**: unicorn ≥66 requires eslint ≥10.4,
but CNA pins `eslint ^9`; `^65` is the last line supporting eslint ≥9.38. This is a **compat-driven
pin, not a fixed fact** — **check the eslint major CNA emits first** (the pin exists only because CNA
pins `eslint ^9`): if the scaffold now ships eslint 10+, drop the `^65` ceiling and install current
unicorn instead (SKILL §6, "version literals are snapshots"). The `lint` script passes
`--max-warnings=0`.

## tsconfig (key-merge)

CNA's `tsconfig.json` already has `strict: true` (the extra strict-family flags are implied by it —
do not spell them out). Verify `strict: true` is present; no other edit is required.

## Vitest config (verbatim)

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { COVERAGE_THRESHOLDS } from "./scripts/coverage-thresholds.mjs";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } }, // day-1 fix #1
  test: {
    globals: true,
    projects: [
      { test: { name: "unit", environment: "jsdom" } },
      { test: { name: "page-smoke", environment: "node", setupFiles: ["./src/app/page-smoke-setup.ts"] } },
    ],
    coverage: {
      provider: "v8",
      // vitest 4 removed `all:` — `include` alone defines the denominator
      include: ["src/**"],
      exclude: ["src/**/*.test.*", "src/**/*.config.*", "**/*.d.ts"],
      reporter: ["text", "lcov", "json-summary"],
      thresholds: COVERAGE_THRESHOLDS,
    },
  },
});
```

`src/app/page-smoke-setup.ts` (day-1 fix #2):

```ts
import { vi } from "vitest";
vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "geist" }),
  Geist_Mono: () => ({ variable: "geist-mono" }),
}));
```

## Coverage thresholds module

`scripts/coverage-thresholds.mjs` — single source, imported by the vitest config (and any CI gate
script). These numbers are the sanctioned duplication of `references/canon/coverage.md`.

```js
export const COVERAGE_THRESHOLDS = { statements: 85, branches: 71, functions: 76, lines: 86 };
```

## Seed tests

- `src/lib/format.ts` — a 3-branch pure util (e.g. `formatCount(n)`: `0` → "none", `1` → "1 item",
  else → "N items") + `src/lib/format.test.ts` covering all three branches (~100%).
- `src/app/counter.tsx` — a `"use client"` component using `useState` + the util via the `@/*`
  alias + `counter.test.tsx` using `@testing-library/react` `render`/`fireEvent`.
- `src/app/page-smoke.test.tsx` — renders `page.tsx`/`layout.tsx` via `renderToStaticMarkup` under
  the page-smoke project (the `next/font` mock is already applied by the setup file).

Read assertion strings from the actual generated code, never hardcode them from this file.

## Playwright e2e

Playwright, one chromium project, `webServer: { command: "npm run dev", url: "http://localhost:3000",
reuseExistingServer: false }`. `reuseExistingServer: false` means the `e2e` gate always drives the
app it built, not whatever already holds the port; if port 3000 is taken, free it or set `PORT` +
the matching `url` before Phase 6.
Browsers are not bundled: run `npx playwright install chromium` **once locally before Phase 6** (the
`e2e` gate cannot pass without it — treat a missing-browser failure as environmental per SKILL §6,
not a code-red), and note in the report that CI needs `npx playwright install --with-deps chromium`
before the e2e job.

## Scripts (key-merge)

Merge into CNA's `scripts` (keep `dev`/`start`):

```jsonc
{
  "lint": "eslint --max-warnings=0",
  "typecheck": "tsc --noEmit",
  "test": "vitest run",
  "coverage": "vitest run --coverage",
  "build": "next build",
  "e2e": "playwright test",
  "audit": "node scripts/audit-gate.mjs",
  "format": "prettier --write .",
  "prepare": "husky"
}
```

## Hooks (verbatim)

`.husky/pre-commit` — the trio. Invoke via `npx --no-install` (resolves the local binary without a
network fetch), **never** a hardcoded `./node_modules/.bin/` prefix — under yarn berry (PnP) there
is no `node_modules/.bin`, so the prefixed form fails at the first line on every commit:

```sh
npx --no-install tsc --noEmit
npx --no-install lint-staged
```

Package-manager note: `npx --no-install` covers npm and pnpm. Under **yarn berry (PnP)** use
`yarn exec tsc --noEmit` / `yarn exec lint-staged`; under **bun**, `bunx tsc --noEmit` /
`bunx lint-staged`. Write the hook for the PM chosen in Phase 0 — the point is a launcher-safe
invocation, never the `.bin` path.

`lint-staged.config.mjs`:

```js
export default {
  "*.{ts,tsx,js,jsx,mjs,cjs}": ["eslint --fix --max-warnings=0 --no-warn-ignored", "prettier --write"],
  "*.{json,md,yml,yaml,css}": "prettier --write",
};
```

## Supply chain + CI delta

- `scripts/audit-gate.mjs` — npm audit JSON gate, fail ≥ moderate, empty allowlist array documented
  at top.
- `.npmrc` → `min-release-age=7`; `.nvmrc` → `24` (keeps corepack), `engines.node` matching.
- CI: the canon workflow (`references/canon/ci-and-sonar.md`) + `actions/setup-node` + `npm ci`; add
  the `playwright install --with-deps chromium` step before the e2e run; sonar property
  `sonar.javascript.lcov.reportPaths=coverage/lcov.info`.

## AGENTS.md

Key-merge: keep CNA's `BEGIN:nextjs-agent-rules`/`END` block untouched and append the canon sections
**below** it (gate contract table from `references/canon/gate-interface.md`, tests-mandatory ethos,
setup prerequisites). Keep CNA's generated `CLAUDE.md` (`@AGENTS.md`).
