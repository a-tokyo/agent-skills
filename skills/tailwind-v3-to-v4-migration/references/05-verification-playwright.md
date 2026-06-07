# 05 — Verifying the migration is a visual no-op (Playwright)

Because a correct migration recompiles identical CSS, the strongest proof is a pixel diff: capture
golden screenshots **on v3 before you start**, then assert the v4 build matches them. Any diff is a
real defect (a missed rename or an un-shimmed changed default), not noise — so keep tolerances tight.

This is optional tooling but high-leverage; for a quick migration, eyeballing the key pages in a
browser for both color schemes is the minimum. The recipe below is framework-agnostic (works against
any served build).

## 1. Stabilize the page (kill nondeterminism)

```ts
// e2e/stabilize.ts
import type { Page } from '@playwright/test';
export async function stabilize(page: Page): Promise<void> {
  await page.addStyleTag({ content: `
    *, *::before, *::after {
      animation-duration: 0s !important; animation-delay: 0s !important;
      transition-duration: 0s !important; transition-delay: 0s !important;
      caret-color: transparent !important; scroll-behavior: auto !important;
    }` });
  await page.evaluate(async () => { try { await (document as Document).fonts.ready; } catch {} });
  await page.evaluate(async () => {           // trigger + settle lazy images
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(r => setTimeout(r, 150));
    window.scrollTo(0, 0);
    await Promise.all(Array.from(document.images).map(img => img.complete ? 0 :
      new Promise(res => { img.addEventListener('load', res, { once: true });
                           img.addEventListener('error', res, { once: true }); })));
  });
  await page.waitForLoadState('networkidle');
}
```

## 2. One spec over every route × color scheme

```ts
// e2e/visual.spec.ts
import { test, expect } from '@playwright/test';
import { stabilize } from './stabilize';
const ROUTES = [ { name: 'home', path: '/' }, /* …all key routes, incl. a prose page… */ ];
for (const r of ROUTES) {
  test(`visual: ${r.name}`, async ({ page }) => {
    const res = await page.goto(r.path, { waitUntil: 'domcontentloaded' });
    expect(res?.status() ?? 200).toBeLessThan(400);
    await stabilize(page);
    await expect(page).toHaveScreenshot(`${r.name}.png`, { fullPage: true, animations: 'disabled' });
  });
}
```

## 3. Config: light + dark projects, production server, tight tolerance

```ts
// playwright.visual.config.ts
import { defineConfig, devices } from '@playwright/test';
const PORT = Number(process.env.VISUAL_PORT ?? 3100); const BASE = `http://localhost:${PORT}`;
const VIEWPORT = { width: 1440, height: 900 };
export default defineConfig({
  testDir: './e2e', testMatch: /visual\.spec\.ts/, fullyParallel: true,
  snapshotPathTemplate: 'e2e/__screenshots__/{projectName}/{arg}{ext}',
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.005, threshold: 0.15 } },
  use: { baseURL: BASE, viewport: VIEWPORT },
  projects: [
    { name: 'light', use: { ...devices['Desktop Chrome'], colorScheme: 'light', viewport: VIEWPORT } },
    { name: 'dark',  use: { ...devices['Desktop Chrome'], colorScheme: 'dark',  viewport: VIEWPORT } },
  ],
  webServer: { command: `<build-then-serve, e.g. yarn start -p ${PORT}>`, url: BASE,
               reuseExistingServer: true, timeout: 120_000 },
});
```

## 4. Workflow

```bash
# on v3, with a production build served:
npx playwright test --config=playwright.visual.config.ts --update-snapshots   # capture goldens
npx playwright test --config=playwright.visual.config.ts                       # sanity: 0 diffs vs itself
# ...migrate to v4, rebuild...
npx playwright test --config=playwright.visual.config.ts                       # must stay 0 diffs
```

Notes:
- Capture goldens against the **production** build (no dev overlays/HMR) for determinism; verify the
  golden run is reproducible (re-run before migrating — it must pass v3-vs-v3).
- Include a `prose`/typography page and pages that exercise borders, focus rings, placeholders, and
  buttons — those surface the changed-default regressions.
- A diff on a single route tells you exactly which shim/rename is missing; open the `-diff.png` in
  `test-results/`.
