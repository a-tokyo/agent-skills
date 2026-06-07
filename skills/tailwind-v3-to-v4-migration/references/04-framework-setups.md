# 04 — Per-framework setup

The breaking changes are universal; only the build wiring differs. After wiring, do Steps 3–5 from
SKILL.md regardless of framework.

## Next.js (App or Pages Router, PostCSS)

- `postcss.config.mjs`:
  ```js
  export default { plugins: { '@tailwindcss/postcss': {} } };
  ```
- Deps: drop `tailwindcss@3`, `autoprefixer`, `postcss-import`; add `tailwindcss@^4`,
  `@tailwindcss/postcss`. No `next.config` change needed.
- CSS entry (`app/globals.css` or `styles/globals.css`) imported once in the root layout/`_app`:
  `@import "tailwindcss";` (+ `@plugin`/`@theme`/`@config` as needed).
- `next/font` keeps working: expose the family as `--font-sans: var(--font-xxx), …;` in `@theme`.

## Vite (React/Vue/Svelte/Solid)

Prefer the dedicated plugin over PostCSS:

```ts
// vite.config.ts
import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({ plugins: [tailwindcss()] });
```

Add dep `@tailwindcss/vite`; remove `autoprefixer`/`postcss-import`. CSS entry: `@import "tailwindcss";`.

## Plain PostCSS (no framework / custom build)

```js
// postcss.config.js
export default { plugins: { '@tailwindcss/postcss': {} } };
```

## Tailwind CLI

`npx tailwindcss -i in.css -o out.css` → `npx @tailwindcss/cli -i in.css -o out.css`
(install `@tailwindcss/cli`).

## Astro

`@astrojs/tailwind` is deprecated for v4 — use the Vite plugin in `astro.config`:

```ts
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({ vite: { plugins: [tailwindcss()] } });
```

## Vue / Svelte / Astro `<style>` blocks & CSS modules

Separately-bundled stylesheets don't see theme vars, custom utilities, or custom variants. Either:

```css
<style>
  @reference "../app.css";   /* imports definitions without duplicating CSS */
  h1 { @apply text-2xl font-bold text-red-500; }
</style>
```

or skip `@apply` and use the generated CSS vars directly (faster):

```css
<style> h1 { color: var(--color-red-500); } </style>
```

**No Sass/Less/Stylus** with v4 — remove preprocessor pipelines for Tailwind-managed styles.

## Monorepos

Repeat Step 0 inventory per package. Each app/package has its own CSS entry + (optional) config; shared
UI packages whose styles are bundled separately need `@reference`. Bump `tailwindcss` everywhere to 4.x
so versions don't split.
