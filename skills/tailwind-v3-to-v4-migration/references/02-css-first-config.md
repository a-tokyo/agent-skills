# 02 — CSS-first config: porting `tailwind.config` to `@theme` (or keeping it via `@config`)

v4 prefers configuration in CSS. You have two valid paths; pick per the JS config's contents.

## Path A — Port `theme.extend` to a CSS `@theme {}` block (idiomatic)

Map nested JS objects to flat CSS custom properties under namespaced prefixes:

| JS config | CSS `@theme` variable |
| --- | --- |
| `colors.brand.500` | `--color-brand-500` |
| `colors.primary.DEFAULT` | `--color-primary` |
| `colors.primary.foreground` | `--color-primary-foreground` |
| `spacing.18` | `--spacing-18` |
| `fontFamily.sans` (array) | `--font-sans` (comma list) |
| `fontFamily.display` | `--font-display` |
| `boxShadow.card` | `--shadow-card` |
| `borderRadius.lg` | `--radius-lg` |
| `screens.3xl` | `--breakpoint-3xl` |
| `zIndex`, `lineHeight`, etc. | `--<namespace>-<key>` |

```css
@import "tailwindcss";

@theme {
  --font-sans: "Inter", system-ui, sans-serif;
  --color-brand-500: #3b82f6;
  --color-brand-600: #2563eb;
  --spacing-18: 4.5rem;
  --shadow-card: 0 2px 1px -1px rgb(0 0 0 / .2), 0 1px 1px 0 rgb(0 0 0 / .14), 0 1px 3px 0 rgb(0 0 0 / .12);
}
```

### `@theme inline` — for variable indirection
When a token's value references another CSS variable (the shadcn / fluxo pattern
`color: hsl(var(--background))`), use `@theme inline` so the utility resolves at the use-site instead
of baking the literal:

```css
@theme inline {
  --color-background: hsl(var(--background));
  --color-foreground: hsl(var(--foreground));
  --radius-lg: var(--radius);
  --radius-md: calc(var(--radius) - 2px);
}
```

After porting, **delete `tailwind.config.*`** (and remove the `content` array — source detection is
automatic).

## Path B — Keep the JS config via `@config` (lowest-risk for plugin theming)

JS config is still supported but no longer auto-detected. Load it explicitly from your CSS entry:

```css
@import "tailwindcss";
@config "../../tailwind.config.ts";
```

**Prefer this when** the config carries theming that's awkward in CSS — most commonly
`@tailwindcss/typography`'s `theme.extend.typography` customization (which defines custom `prose-*`
modifiers and per-element overrides via `theme(colors.…)`). Keeping `@config` preserves those exactly;
a full CSS port would mean re-expressing each `prose` override as CSS variables or a custom variant.

Unsupported even via `@config`: `corePlugins`, `safelist`, `separator`. Replace `safelist` with
`@source inline("bg-red-500 text-center …")` in CSS.

You can also **mix**: keep `@config` for the typography block while moving simple tokens to `@theme`.
If you keep a JS config purely for one plugin, trim it down to just that plugin + its theme.

## Plugins

- Load CSS-side: `@plugin "@tailwindcss/typography";` (or `@plugin "tailwindcss-animate";` etc.).
- Built into core now — remove the dep and any usage: `@tailwindcss/container-queries`,
  `@tailwindcss/aspect-ratio`, line-clamp.
- Bump `@tailwindcss/typography` to a v4-compatible release (≥0.5.16) or the build fails.

## Custom utilities / components

`@layer utilities { .tab-4 { … } }` and `@layer components { .btn { … } }` no longer register as
Tailwind utilities (v4 uses native cascade layers). Use the `@utility` API:

```css
@utility tab-4 { tab-size: 4; }
@utility btn  { border-radius: .5rem; padding: .5rem 1rem; background: ButtonFace; }
```

`@utility` classes participate in variants and are sorted by property count, so component-like
utilities can still be overridden by single-property utilities.

**Container:** v3's `theme.container.center`/`padding` options are gone — recreate the same way:

```css
@utility container { margin-inline: auto; padding-inline: 2rem; }
```

**Directive order:** keep `@import "tailwindcss";` first, then `@config "…";` / `@theme { … }` after it.

## Theme values in JS

`resolveConfig()` is gone. Read a resolved token at runtime from the generated CSS variable:

```js
const shadow = getComputedStyle(document.documentElement).getPropertyValue("--shadow-xl");
// libraries can animate to a token directly: animate={{ backgroundColor: "var(--color-blue-500)" }}
```
