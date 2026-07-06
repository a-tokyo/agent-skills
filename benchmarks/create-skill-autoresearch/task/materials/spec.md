# Conventional Commits v1.0.0 — condensed reference

Structure:

```
<type>[optional scope][!]: <description>

[optional body]

[optional footer(s)]
```

- **type**: `feat` (new feature, SemVer MINOR), `fix` (bug fix, SemVer PATCH), plus
  `build`, `chore`, `ci`, `docs`, `style`, `refactor`, `perf`, `test`.
- **scope**: a noun in parentheses describing the section of the codebase, e.g. `feat(parser):`.
- **breaking changes**: append `!` after the type/scope, and/or add a `BREAKING CHANGE: <description>`
  footer. A breaking change can belong to any type. SemVer MAJOR.
- **description**: short summary immediately after the colon and space.
- **body**: free-form, one blank line after the description.
- **footers**: `Token: value` lines (e.g. `Reviewed-by:`, `Refs:`), one blank line after the body.
- **reverts**: the spec leaves revert handling to tooling conventions; a common pattern is a
  `revert:` prefix with a body referencing the reverted commit SHA.

Source: conventionalcommits.org/en/v1.0.0 (condensed; team conventions in the gold
standards take precedence where stricter).
