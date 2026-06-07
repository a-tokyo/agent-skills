# 01 — Complete breaking-change tables

The authoritative lookup for every v3→v4 change. The codemod handles most; use this to audit and to
migrate by hand when the tool can't run.

## Renamed utilities (pure aliases — same compiled CSS)

| v3 | v4 |
| --- | --- |
| `shadow-sm` | `shadow-xs` |
| `shadow` | `shadow-sm` |
| `drop-shadow-sm` | `drop-shadow-xs` |
| `drop-shadow` | `drop-shadow-sm` |
| `blur-sm` | `blur-xs` |
| `blur` | `blur-sm` |
| `backdrop-blur-sm` | `backdrop-blur-xs` |
| `backdrop-blur` | `backdrop-blur-sm` |
| `rounded-sm` | `rounded-xs` |
| `rounded` | `rounded-sm` |
| `outline-none` | `outline-hidden` |
| `ring` | `ring-3` |

**Ordering rule:** when editing by hand, rename the explicit `-sm` to `-xs` *before* the bare name to
`-sm`, or you double-apply. Use word boundaries so `rounded`→`rounded-sm` doesn't touch `rounded-md`,
`rounded-full`, `rounded-lg`, etc. The official codemod handles this correctly — prefer it.

`outline-none` semantics: v3 `outline-none` set an *invisible* outline (kept for forced-colors a11y).
v4 renames that to `outline-hidden`; the new `outline-none` actually sets `outline-style: none`. Also
`outline-<n>` now implies `outline-style: solid`, so `outline outline-2` → `outline-2`.

## Removed utilities (must rewrite — no alias)

| Removed | Replacement |
| --- | --- |
| `bg-opacity-*` | opacity modifier: `bg-black/50` |
| `text-opacity-*` | `text-black/50` |
| `border-opacity-*` | `border-black/50` |
| `divide-opacity-*` | `divide-black/50` |
| `ring-opacity-*` | `ring-black/50` |
| `placeholder-opacity-*` | `placeholder-black/50` |
| `flex-shrink-*` | `shrink-*` |
| `flex-grow-*` | `grow-*` |
| `overflow-ellipsis` | `text-ellipsis` |
| `decoration-slice` | `box-decoration-slice` |
| `decoration-clone` | `box-decoration-clone` |

## Gradients

- `bg-gradient-to-{t,tr,r,br,b,bl,l,tl}` → `bg-linear-to-{…}`.
- New in v4: `bg-radial`, `bg-conic`, angled `bg-linear-45`, interpolation `bg-linear-to-r/oklch`.
- Variants now **preserve** gradient stops (v3 reset them). To unset a 3-stop back to 2-stop in a state,
  use `via-none`, e.g. `… via-orange-400 dark:via-none …`.

## Space-between & divide selectors (layout change, no shim)

For performance, `space-x-*`/`space-y-*` and `divide-x-*`/`divide-y-*` changed selector:

```css
/* v3 */ .space-y-4 > :not([hidden]) ~ :not([hidden]) { margin-top: 1rem; }
/* v4 */ .space-y-4 > :not(:last-child)               { margin-bottom: 1rem; }
```

You'll see shifts only if you used these with **inline** elements, or added other margins/borders to
specific children. There's no compat shim — if it breaks, migrate that block to flex/grid + `gap`.

## Container configuration (removed options)

v3's `theme.container.center` / `theme.container.padding` no longer exist. Recreate with `@utility`:

```css
@utility container { margin-inline: auto; padding-inline: 2rem; }
```

Forget this and every `container` silently loses its centering/padding.

## Prefix

v3 `prefix: 'tw'` produced `tw-flex`; v4 uses a variant-style prefix at the front: `tw:flex`. Configure
the theme as if unprefixed: `@import "tailwindcss" prefix(tw);`. If the project uses a prefix, grep for
the old dash form (`grep -rEn '\btw-[a-z]' src`) and convert every class, or the whole UI breaks.

## Deprecated `screen-*` max-widths

`max-w-screen-{sm,md,lg,xl,2xl}` are deprecated in v4 (they still compile for now, so a visual diff
won't flag them). Modernize to the breakpoint theme variable: `max-w-screen-xl` → `max-w-(--breakpoint-xl)`.
The codemod does this automatically.

## Syntax changes

| Concern | v3 | v4 |
| --- | --- | --- |
| CSS var in arbitrary value | `bg-[--brand]` | `bg-(--brand)` |
| commas in grid/object arbitrary | `grid-cols-[max-content,auto]` | `grid-cols-[max-content_auto]` |
| important modifier | `!flex` | `flex!` |
| prefix | `tw-flex` (config prefix) | `tw:flex` (variant-style, front) |
| variant stacking order | right→left: `first:*:pt-0` | left→right: `*:first:pt-0` |

## Transforms & transitions

- `rotate-*`, `scale-*`, `translate-*` now map to the individual CSS properties.
- `transform-none` no longer resets them — reset individually: `scale-none`, `rotate-none`,
  `translate-none`.
- If you customized the transition property list with `transform`
  (`transition-[opacity,transform]`), switch to the individual property
  (`transition-[opacity,scale]`) or the utilities won't transition.
- `transition`/`transition-colors` now include `outline-color`. Set the outline color unconditionally
  (or for both states) to avoid an unwanted color transition on focus.

## Behavior / preflight changes (see also 03-compat-shims)

- Default border & divide color → `currentColor` (was `gray-200`).
- `ring` width 3px→1px, color `blue-500`→`currentColor`.
- Placeholder color → current text @ 50% (was `gray-400`).
- Buttons → `cursor: default` (was `pointer`).
- `<dialog>` margins reset (were `auto`).
- `hidden` attribute now wins over `display` utilities (`block`/`flex`). Remove `hidden` to show.
  (Exception: `hidden="until-found"`.)
- `hover` only applies on `(hover: hover)` devices.

## Config-level removals

- `content` array — gone (automatic source detection, respects `.gitignore`, skips binaries).
- `corePlugins` — unsupported.
- JS `safelist` / `separator` — unsupported (`safelist` → `@source inline(...)` in CSS).
- `resolveConfig()` export — removed (read generated CSS vars via `getComputedStyle`).
- `theme()` dot notation — prefer CSS vars; in media queries use `theme(--breakpoint-xl)`.
