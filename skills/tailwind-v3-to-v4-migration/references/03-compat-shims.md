# 03 — Compat shims for v4's changed defaults

These are the only changes that alter rendered pixels. For each, decide *do I rely on the v3 behavior?*
If yes, paste the shim into your main CSS **after** `@import "tailwindcss";`. If no, prefer migrating to
the explicit utility (it's the idiomatic v4 fix) and skip the shim.

The honest default for an existing app whose look must not change: add the border, placeholder, and
button-cursor shims (they're the ones most apps unknowingly depend on), then let the visual-parity
check tell you if any other is needed.

## Border / divide default color → `currentColor` (was `gray-200`)

Idiomatic fix: add an explicit color wherever you use `border`/`divide` (`border border-gray-200`).
Whole-project compat shim:

```css
@layer base {
  *, ::after, ::before, ::backdrop, ::file-selector-button {
    border-color: var(--color-gray-200, currentColor);
  }
}
```

## Ring width 3px→1px and color blue-500→currentColor

Idiomatic fix: `ring`→`ring-3`, and add `ring-blue-500` where you relied on the blue default.
Compat-only escape (documented as non-idiomatic):

```css
@theme {
  --default-ring-width: 3px;
  --default-ring-color: var(--color-blue-500);
}
```

## Placeholder color → current text @ 50% (was `gray-400`)

```css
@layer base {
  input::placeholder, textarea::placeholder { color: var(--color-gray-400); }
}
```

## Buttons → `cursor: default` (was `pointer`)

```css
@layer base {
  button:not(:disabled), [role="button"]:not(:disabled) { cursor: pointer; }
}
```

## `<dialog>` margins reset (were auto-centered)

```css
@layer base { dialog { margin: auto; } }
```

## Hover only on hover-capable devices

v4 wraps `hover:` in `@media (hover: hover)`. If your UX depends on tap triggering hover on touch
devices, restore the old behavior:

```css
@custom-variant hover (&:hover);
```

(Generally treat hover as an enhancement instead.)

## Dark mode selector

- v3 `darkMode: 'media'` → v4 default is identical (`prefers-color-scheme`). **No shim.**
- v3 `darkMode: 'class'` → add:
  ```css
  @custom-variant dark (&:is(.dark, .dark *));
  ```
- v3 custom selector (e.g. `[data-theme="dark"]`) → mirror it:
  ```css
  @custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));
  ```

## Transitioning `outline-color` (behavioral — invisible to a static screenshot)

`transition` and `transition-colors` now include `outline-color`. If you add a focus outline with a
custom color (e.g. `transition hover:outline-2 hover:outline-cyan-500`), the color now animates from the
default. There's no shim — set the outline color unconditionally so both states share it:

```html
<button class="outline-cyan-500 transition hover:outline-2">…</button>
```

A pixel-diff of static states won't catch this (it's a temporal effect) — check focus/hover by hand.

## `hidden` attribute now wins over display utilities

Not a CSS shim — a markup fix. If an element with the `hidden` attribute was being shown by a `block`/
`flex` class, remove the `hidden` attribute. (`hidden="until-found"` is unaffected.)
