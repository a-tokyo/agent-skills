# 06 — Gotchas & field notes

Hard-won specifics that turn a "looks done" migration into a correct one.

## Rename ordering (manual edits only)
Rename explicit `-sm`→`-xs` **before** bare→`-sm`, for every scale (`shadow`, `rounded`, `blur`,
`drop-shadow`, `backdrop-blur`). Reverse order double-applies. Always word-boundary the bare form:
`\brounded(?=["'`\s])` so you don't rewrite `rounded-md`/`rounded-full`/`rounded-lg`. The official
codemod gets this right — prefer it; hand-edit only the leftovers it reports.

## The typography (`prose`) port — the classic trap
`@tailwindcss/typography` is still a plugin in v4 (`@plugin "@tailwindcss/typography";`) and must be on
a v4-compatible release (≥0.5.16) or the build errors. If the v3 config customized prose via
`theme.extend.typography` — e.g. a `DEFAULT` block and a custom `dark` key that becomes a `prose-dark`
modifier used as `prose dark:prose-dark` — the **lowest-risk correct path is to keep that block in the
JS config and load it with `@config`**, rather than re-expressing every `theme(colors.…)` override in
CSS. Built-in `prose-invert` and color modifiers (`prose-red`) keep working either way. Verify on an
actual rendered article in both light and dark — prose regressions are easy to miss.

## Gradients preserve stops across variants
v3 reset the whole gradient when a variant overrode part of it; v4 keeps the other stops. If you had
`from-red-500 to-yellow-400 dark:from-blue-500` expecting `to` to vanish in dark, you now need
`dark:via-none` (for 3-stop) or to set the stop explicitly. Rename `bg-gradient-to-*`→`bg-linear-to-*`.

## `space-*`/`divide-*` & `container` can shift layout without a shim
Two screenshot-visible changes have no compat shim. `space-x/y-*` and `divide-x/y-*` now target
`:not(:last-child)` — if a list shifts (especially with inline children or hand-tuned child margins),
rewrite it to flex/grid + `gap`. And v3's `theme.container.center`/`padding` are gone — recreate via
`@utility container { margin-inline: auto; padding-inline: 2rem; }` or every `container` loses its
centering. If a visual diff appears on a list or a centered wrapper, check these before hunting shims.

## Prefix migration breaks everything if missed
A project with `prefix: 'tw'` emits `tw-flex` in v3 but needs `tw:flex` in v4. The codemod may not
convert these in every template form — grep `\btw-[a-z]` (your prefix) and confirm. A missed prefix
means the entire stylesheet stops matching the markup.

## `aspect-[x/y]` is fine; the aspect-ratio *plugin* is not needed
Arbitrary aspect ratios (`aspect-[2/1]`) are core and unchanged. `@tailwindcss/aspect-ratio` only
provided the old `aspect-w-*`/`aspect-h-*` API — if those aren't used, remove the plugin/dep. (You may
optionally tidy `aspect-[2/1]`→`aspect-2/1`, but it's not required.)

## Dead config entries
v3 projects often carry unused `theme.extend` (e.g. `backgroundImage.gradient-radial/conic` that v4
ships as `bg-radial`/`bg-conic`). Grep usage before porting; drop what nothing references instead of
translating it.

## `darkMode: 'media'` needs nothing
Don't add `@custom-variant dark` if v3 used `media` — v4's default already keys off
`prefers-color-scheme`. Adding the class variant there would *break* media-driven dark mode.

## Hover on touch devices
`hover:` now lives under `@media (hover: hover)`. Carousels/menus that relied on tap=hover need
`@custom-variant hover (&:hover);`.

## Important & arbitrary-value syntax slip through codemods
Double-check `!flex`→`flex!`, `bg-[--x]`→`bg-(--x)`, and comma→underscore in
`grid-cols-[…,…]`/`object-*`. These are easy for find/replace passes to miss in template strings and
`clsx`/`cva` calls.

## Multiple CSS entry points / monorepos
There can be more than one file with `@tailwind`/`@import "tailwindcss"`. Migrate every one; a missed
entry compiles half the app against v4 and the rest against nothing. Bump `tailwindcss` to 4.x in every
package so versions don't split.

## Verify the lockfile actually moved to 4.x
After reinstalling, confirm the resolved version: a stale lockfile can keep 3.x pinned and the build
will still emit `@tailwind`-era CSS, masking an incomplete migration.

## Don't accept a visual diff
Every pixel diff after migration maps to a concrete cause (missed rename or un-shimmed default). Chase
it to root cause; "close enough" hides real regressions (most often borders, rings, or placeholders).
