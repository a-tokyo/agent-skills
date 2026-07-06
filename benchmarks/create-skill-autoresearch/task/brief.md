# Build brief: `conventional-commits`

This brief answers every factory interview question. Do not ask the user anything —
work non-interactively from this document.

## Purpose (1.1)

Build a skill named `conventional-commits` that converts a **staged diff summary**
(file list + per-file change notes) into a single commit message following this
team's Conventional Commits conventions.

- Domain: git commit-message generation, Conventional Commits v1.0.0 plus team conventions
- Target user: an AI coding agent that has just staged changes and must emit the commit message verbatim
- Success: the emitted message matches the team's conventions exactly — right type, right scope,
  correct breaking-change markers, correct subject style, correct body/footer format

## Gold standards (1.2)

Six input/output pairs in `input/`: `input-01.md`..`input-06.md` are staged diff
summaries, `output-01.md`..`output-06.md` are the exact commit messages the team wrote
for them. These encode team conventions beyond the public spec — study them closely.

## Study materials (1.3)

- `materials/spec.md` — a condensed Conventional Commits v1.0.0 reference
- The gold standards above (primary source for team-specific conventions)

## Scope and constraints (1.4)

- SKILL.md body under **150 lines**; no `references/` unless truly forced
- No integration skills
- Anti-pattern: generic spec-restating advice that ignores the team conventions in the gold standards
- Invocation mode: **user-invoked** — the human always types the skill name; the agent
  must never fire it on its own (`disable-model-invocation: true`)
- Evaluation budget, if applicable: `target_score: 0.85`, `max_iterations: 6`

## Existing skill (1.5)

None. Greenfield.

## Output location

Write the finished skill to `output/conventional-commits/SKILL.md` (plus
`output/conventional-commits/references/` only if unavoidable) inside the current
working directory.
