#!/usr/bin/env bash
# fixtures/make-golden.sh <stack> [out-dir]
#
# Generates the "golden" fixture: official scaffolder output + the FULL guardrail canon, hand-built
# directly from DESIGN.md's adapter specs (§8.6-8.10) and research/05-09 -- independent of whatever
# SKILL.md text ticket 12 eventually writes. This is the scorer's acceptance oracle: if evaluate.sh
# can't score this fixture >=95, the scorer (not the skill) has a bug.
#
# Per ticket 10's time-box: only `next` is fully implemented (must reach >=95, verified by
# test-checker.mjs). nest/django/go/rust are TODO stubs -- exit 2 so a caller can never mistake "not
# implemented" for "scored 0" (a real 0 would be a scorer bug; a TODO is an honest gap).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK="${1:?usage: make-golden.sh <next|nest|django|go|rust> [out-dir]}"
OUT="${2:-$HERE/.gen/${STACK}-golden}"

GIT_AUTHOR=(-c user.name="guardrails-benchmark" -c user.email="guardrails-benchmark@localhost")
commit_all() { ( cd "$1" && git "${GIT_AUTHOR[@]}" add -A && git "${GIT_AUTHOR[@]}" commit -q -m "$2" ); }

# ---- real, currently-tagged commit SHAs (git ls-remote, resolved at authoring time — see README for
#      the re-resolve command; a stale SHA is a maintenance issue, not a scorer-fairness issue, since
#      the scorer only checks *shape* — 40 hex chars — not that the SHA is the latest) ----
SHA_CHECKOUT="34e114876b0b11c390a56381ad16ebd13914f8d5"       # actions/checkout v4.3.1
SHA_SETUP_NODE="49933ea5288caeca8642d1e84afbd3f7d6820020"    # actions/setup-node v4.4.0
SHA_SONAR_SCAN="713881670b6b3676cda39549040e2d88c70d582e"    # SonarSource/sonarqube-scan-action v8.2.0
SHA_SONAR_GATE="cf038b0e0cdecfa9e56c198bbb7d21d751d62c3b"    # sonarsource/sonarqube-quality-gate-action v1.2.0

make_next_golden() {
  rm -rf "$OUT"
  mkdir -p "$(dirname "$OUT")"

  echo "# [1/9] scaffold: create-next-app" >&2
  npx --yes create-next-app@latest "$OUT" \
    --ts --app --tailwind --eslint --src-dir --import-alias "@/*" \
    --use-npm --disable-git --yes

  cd "$OUT"
  git init -q -b main

  echo "# [2/9] devDependencies: static analysis + test + e2e + hooks" >&2
  npm install --no-fund --no-audit --loglevel=error --save-dev \
    eslint-plugin-sonarjs eslint-plugin-unicorn@^65.0.1 eslint-plugin-unused-imports \
    eslint-plugin-tsdoc eslint-config-prettier \
    vitest @vitejs/plugin-react @testing-library/react @testing-library/jest-dom jsdom @vitest/coverage-v8 \
    @playwright/test \
    husky lint-staged

  echo "# [3/9] ESLint: replace eslint.config.mjs (sonarjs + unicorn + unused-imports + tsdoc, --max-warnings=0)" >&2
  cat > eslint.config.mjs <<'EOF'
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettierFlat from "eslint-config-prettier/flat";
import sonarjs from "eslint-plugin-sonarjs";
import unicorn from "eslint-plugin-unicorn";
import unusedImports from "eslint-plugin-unused-imports";
import tsdoc from "eslint-plugin-tsdoc";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  prettierFlat,
  sonarjs.configs.recommended,
  {
    plugins: { unicorn, "unused-imports": unusedImports, tsdoc },
    rules: {
      "sonarjs/cognitive-complexity": ["error", 15],
      "unused-imports/no-unused-imports": "error",
      // canon-faithful: ^_ escape hatch exactly like the capexlog canon (adapters/next.md) — the
      // teeth lint mutation deliberately uses a NON-underscore name so this pattern never masks it
      "unused-imports/no-unused-vars": ["error", { varsIgnorePattern: "^_", argsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }],
      "tsdoc/syntax": "error",
      "unicorn/prefer-node-protocol": "error",
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "coverage/**", "playwright-report/**"]),
]);

export default eslintConfig;
EOF
  npm pkg set 'devDependencies.eslint-plugin-unicorn=^65.0.1' >/dev/null

  echo "# [4/9] tsconfig: CNA already ships strict:true (verified — no edit needed)" >&2

  echo "# [5/9] Vitest + coverage thresholds + resolve.alias (day-1 pre-fix) + next/font mock (day-1 pre-fix)" >&2
  mkdir -p src/lib
  cat > vitest.config.ts <<'EOF'
import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // @vitejs/plugin-react does NOT read tsconfig.json "paths" — mandatory day-1 pre-fix
    // (research/05 §4), NOT optional.
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
    coverage: {
      provider: "v8",
      // vitest 4 removed `all` — `include` alone now drives which files are instrumented even if a
      // file isn't imported by any test (verified against the installed 4.1.9 CoverageOptions type).
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/**/*.d.ts", "src/app/layout.tsx", "src/app/page.tsx"],
      thresholds: { statements: 85, branches: 71, functions: 76, lines: 86 },
    },
  },
});
EOF
  cat > vitest.setup.ts <<'EOF'
import "@testing-library/jest-dom/vitest";
EOF

  cat > src/lib/format.ts <<'EOF'
/** Formats a count for display. Three real branches — the seed's coverage surface. */
export function formatCount(count: number): string {
  if (count < 0) {
    return "invalid";
  }
  if (count === 0) {
    return "none";
  }
  return `${count} item${count === 1 ? "" : "s"}`;
}
EOF
  cat > src/lib/format.test.ts <<'EOF'
import { describe, expect, it } from "vitest";
import { formatCount } from "./format";

describe("formatCount", () => {
  it("flags negative counts as invalid", () => {
    expect(formatCount(-1)).toBe("invalid");
  });
  it("reports zero as none", () => {
    expect(formatCount(0)).toBe("none");
  });
  it("pluralizes counts above one", () => {
    expect(formatCount(1)).toBe("1 item");
    expect(formatCount(2)).toBe("2 items");
  });
});
EOF

  cat > src/app/counter.tsx <<'EOF'
"use client";
import { useState } from "react";
import { formatCount } from "@/lib/format";

export function Counter() {
  const [count, setCount] = useState(0);
  return (
    <div>
      <p data-testid="count-label">{formatCount(count)}</p>
      <button type="button" onClick={() => setCount((c) => c + 1)}>
        increment
      </button>
      <button type="button" onClick={() => setCount((c) => c - 1)}>
        decrement
      </button>
    </div>
  );
}
EOF
  cat > src/app/counter.test.tsx <<'EOF'
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Counter } from "./counter";

describe("Counter", () => {
  it("starts at none and increments/decrements", () => {
    render(<Counter />);
    expect(screen.getByTestId("count-label")).toHaveTextContent("none");
    fireEvent.click(screen.getByText("increment"));
    expect(screen.getByTestId("count-label")).toHaveTextContent("1 item");
    fireEvent.click(screen.getByText("decrement"));
    expect(screen.getByTestId("count-label")).toHaveTextContent("none");
  });
});
EOF

  cat > src/app/page-smoke-setup.ts <<'EOF'
import { vi } from "vitest";

// next/font/google cannot execute outside Next's own build pipeline (research/05 §4, live-verified
// failure) — any test importing layout.tsx must mock it before import.
vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "--font-geist-sans" }),
  Geist_Mono: () => ({ variable: "--font-geist-mono" }),
}));
EOF
  cat > src/app/page-smoke.test.tsx <<'EOF'
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import "./page-smoke-setup";

describe("page smoke", () => {
  it("renders the home page without throwing", async () => {
    const { default: Page } = await import("./page");
    expect(() => renderToStaticMarkup(<Page />)).not.toThrow();
  });
  it("renders the root layout without throwing", async () => {
    const { default: Layout } = await import("./layout");
    expect(() =>
      renderToStaticMarkup(
        <Layout>
          <div />
        </Layout>,
      ),
    ).not.toThrow();
  });
});
EOF

  echo "# [6/9] Playwright e2e" >&2
  mkdir -p e2e
  cat > playwright.config.ts <<'EOF'
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  webServer: { command: "npm run dev", url: "http://localhost:3000", reuseExistingServer: !process.env.CI },
  use: { baseURL: "http://localhost:3000" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
EOF
  cat > e2e/home.spec.ts <<'EOF'
import { expect, test } from "@playwright/test";

test("home page loads", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/.+/);
});
EOF

  echo "# [7/9] gate scripts + supply chain + hooks" >&2
  npm pkg set scripts.lint="eslint . --max-warnings=0"
  npm pkg set scripts.typecheck="tsc --noEmit"
  npm pkg set scripts.test="vitest run"
  npm pkg set scripts.coverage="vitest run --coverage"
  npm pkg set scripts.e2e="playwright test"
  npm pkg set scripts.audit="npm audit --audit-level=moderate"
  npm pkg set scripts.prepare="husky"
  npm pkg set scripts.format="prettier --write ." 2>/dev/null || true

  printf '22\n' > .nvmrc
  npm pkg set engines.node=">=20 <25"
  printf 'min-release-age=7\n' > .npmrc

  npx --yes husky init >/dev/null 2>&1 || mkdir -p .husky
  cat > .husky/pre-commit <<'EOF'
npm run typecheck
npx lint-staged
EOF
  chmod +x .husky/pre-commit
  cat > .lintstagedrc.json <<'EOF'
{
  "*.{ts,tsx}": ["eslint --max-warnings=0", "vitest related --run"]
}
EOF

  echo "# [8/9] CI workflow (all 7 gates, SHA-pinned, SONAR_ENABLED-gated sonar job)" >&2
  mkdir -p .github/workflows
  cat > .github/workflows/ci.yml <<EOF
name: ci
on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

concurrency:
  group: \${{ github.workflow }}-\${{ github.ref }}
  cancel-in-progress: true

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@${SHA_CHECKOUT} # v4.3.1
      - uses: actions/setup-node@${SHA_SETUP_NODE} # v4.4.0
        with: { node-version-file: ".nvmrc", cache: "npm" }
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck
      - run: npm run test
      - run: npm run coverage
      - run: npm run build
      - run: npx playwright install --with-deps chromium
      - run: npm run e2e
      - run: npm run audit

  sonar:
    needs: gate
    if: \${{ vars.SONAR_ENABLED == 'true' }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@${SHA_CHECKOUT} # v4.3.1
      - uses: actions/setup-node@${SHA_SETUP_NODE} # v4.4.0
        with: { node-version-file: ".nvmrc", cache: "npm" }
      - run: npm ci
      - run: npm run coverage
      - uses: SonarSource/sonarqube-scan-action@${SHA_SONAR_SCAN} # v8.2.0
        env: { SONAR_TOKEN: \${{ secrets.SONAR_TOKEN }} }
      - uses: sonarsource/sonarqube-quality-gate-action@${SHA_SONAR_GATE} # v1.2.0
        env: { SONAR_TOKEN: \${{ secrets.SONAR_TOKEN }} }
EOF
  cat > sonar-project.properties <<'EOF'
sonar.projectKey=app-ai-guardrails-next-golden
sonar.javascript.lcov.reportPaths=coverage/lcov.info
sonar.sources=src
EOF

  echo "# [9/9] agent surface: AGENTS.md gate table, .claude/skills, .agents/" >&2
  cat >> AGENTS.md <<'EOF'

## Guardrail gate contract

| Gate | Command |
|---|---|
| lint | `npm run lint` |
| typecheck | `npm run typecheck` |
| test | `npm run test` |
| coverage | `npm run coverage` |
| build | `npm run build` |
| e2e | `npm run e2e` |
| audit | `npm run audit` |

Tests are mandatory; never lower a coverage threshold or delete/skip a test to go green.
EOF
  mkdir -p .agents/plans .claude/skills
  touch .agents/plans/.gitkeep .claude/skills/.gitkeep
  cat > skills-lock.json <<'EOF'
{ "skills": [] }
EOF

  echo "# verifying gates before commit (Phase 6/7 rehearsal)" >&2
  npm run lint
  npm run typecheck
  npm run test
  npm run coverage
  npm run build

  commit_all "$OUT" "golden: full guardrail canon applied to create-next-app scaffold"
  echo "# golden Next.js fixture ready: $OUT" >&2
}

case "$STACK" in
  next) make_next_golden ;;
  nest|django|go|rust)
    echo "TODO: make-golden.sh has no generator for stack '$STACK' yet (ticket 10 time-box: only next is implemented, per DESIGN.md handoff notes)." >&2
    exit 2
    ;;
  *)
    echo "unknown stack: $STACK" >&2
    exit 2
    ;;
esac
