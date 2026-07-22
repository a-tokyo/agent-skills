# Adapter — Next.js (App Router, TypeScript)

The deep reference. Templates carry no exact app-dep versions (the lockfile pins reality); the one
exception is a compatibility-mandated range pin with a rationale comment.

## Contents

- Scaffold (create-next-app)
- Package-manager divergence
- Day-1 pre-fixes (both live-verified)
- ESLint (replace)
- Org preset instead of inline config
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
      // explicit {} defeats flat-config option retention: a severity-only override keeps any
      // options an earlier spread set for the same rule (e.g. an allow-list on no-console) —
      // pass explicit options whenever overriding a rule a base config may have configured.
      "no-console": ["error", {}],
      "sonarjs/prefer-read-only-props": "off", // Readonly<> on every React prop is noise, not safety
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}", "tests/**", "e2e/**", "**/*.config.*"],
    rules: {
      "sonarjs/no-hardcoded-passwords": "off",
      "sonarjs/pseudo-random": "off",
      "sonarjs/no-alphabetical-sort": "off", // fixture ordering in tests is fine
      "no-console": "off",
    },
  },
  {
    // ambient `declare var` is the only legal globalThis augmentation form
    files: ["**/*.d.ts"],
    rules: { "no-var": "off", "vars-on-top": "off" },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "coverage/**"]),
]);
```

`eslint-plugin-unicorn` is pinned in `package.json` `devDependencies` as
`"eslint-plugin-unicorn": "^65"` **with this rationale comment**: unicorn ≥66 requires eslint ≥10.4,
but CNA pins `eslint ^9`; `^65` is the last line supporting eslint ≥9.38. This is a **compat-driven
pin, not a fixed fact** — **check the eslint major CNA emits first** (the pin exists only because CNA
pins `eslint ^9`): if the scaffold now ships eslint 10+, drop the `^65` ceiling and install current
unicorn instead (SKILL §6, "version literals are snapshots"). **ESLint ≥10 escape route:**
`eslint-config-next` drags `eslint-plugin-react`/`import` versions that are incompatible with
ESLint 10 — if the scaffold crosses that line, replace the config-next base spreads with
`@next/eslint-plugin-next` used directly (`plugins: { '@next/next': nextPlugin }` + its
`recommended` and `core-web-vitals` rule sets) and keep everything else identical. The `lint`
script passes `--max-warnings=0`. `sonarjs/deprecation` stays ON (a guardrails scaffold wants the
deprecation signal; teams drowning in dependency-rename churn may disable it with a rationale
comment — never silently).

## Org preset instead of inline config

If the org has a shared lint-preset package (Phase 0 parameter — e.g. the published
`@zoldytech/javascript`), **prefer it** over the inline config above — one canonical bar, and a fix
propagates to every repo in one release instead of N edits. The consumer shape that works:

```js
// eslint.config.mjs — the ENTIRE file
import { next } from "@zoldytech/javascript/eslint";

export default next({
  typeChecked: true, // if the preset gates a type-aware ruleset (needs tsconfig include in sync)
  ignores: [".next/**", "coverage/**"],
  overrides: [
    // ONLY genuinely repo-specific blocks belong here (runtime constraints, test dirs) —
    // anything universal goes upstream into the preset, not copied between repos
  ],
});
```

- The preset exports a **factory** (options in, composed flat config out) with an `overrides`
  escape hatch — the per-repo file holds composition + local overrides, nothing else.
- **The preset owns its own peer floors** — ESLint, `unicorn`, Node. Adopting one means matching
  its required versions (e.g. `@zoldytech/javascript` requires ESLint 10.4+ and bundles its own
  `unicorn`); **drop the inline `eslint-plugin-unicorn` pin above** and don't re-pin those peers by
  hand — the preset resolves them. That inline pin applies only to the no-preset path.
- Adoption is **à la carte**: eslint, prettier (`"prettier": "@zoldytech/javascript/prettier"` in
  package.json), and tsconfig presets are independently adoptable; take what fits.
- Published presets install from the registry
  (`"@zoldytech/javascript": "^0.1.4"`) like any dependency. Private/unpublished presets distribute
  fine as a git tag dependency instead (`"@zoldytech/javascript": "github:zoldytech/javascript#0.1.4"`)
  — no registry needed.
- Turning a strict shared bar on over existing code surfaces a backlog: burn it down, or use
  ESLint's native suppressions as a shrinking baseline (`--suppress-all` to record it, prune with
  `--pass-on-unpruned-suppressions`; verify the flags against `eslint --help` at use time — §6
  currency rule) — never weaken the preset itself.

If no preset exists: use the inline config above; an org with more than one repo should extract
one (this section is the target shape).

## tsconfig (key-merge)

CNA's `tsconfig.json` already has `strict: true` (the extra strict-family flags are implied by it —
do not spell them out). Verify `strict: true` is present, and add one key:
`"noFallthroughCasesInSwitch": true` (real bug-catcher, not implied by `strict`, generated code
complies). `noUncheckedIndexedAccess` is a DELIBERATE omission: it turns every index access into
`T | undefined`, which breaks scaffolder-generated code and seed greenness — do not add it.

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
export const COVERAGE_THRESHOLDS = { statements: 90, branches: 85, functions: 90, lines: 90 };
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
