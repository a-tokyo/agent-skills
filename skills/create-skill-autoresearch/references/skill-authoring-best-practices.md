# Skill-Authoring Best Practices

The official rules every skill this factory produces must follow. Distilled from Anthropic's
[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).
This factory **extends** the official single-pass creators (Anthropic's `skill-creator`, Cursor's
`create-skill`) — it does not replace their conventions. When this file and the official doc
disagree, the official doc wins; update this file.

## Contents
- Hard constraints (the Phase-3 pre-flight checklist)
- Description quality
- Conciseness and degrees of freedom
- Progressive disclosure
- Workflows and feedback loops
- Content guidelines
- Scripts (skills with executable code)
- Evaluation

## Hard constraints (the Phase-3 pre-flight checklist)

Enforce these before drafting. They are validated as deterministic checks in the self-test and
asserted by the panel in Phase 5 — not optional.

- **`name`**: ≤ 64 characters; lowercase letters, numbers, and hyphens only; no XML tags;
  **no reserved words `anthropic` or `claude`**. Prefer gerund form (`processing-pdfs`,
  `analyzing-spreadsheets`); noun phrases and action verbs are acceptable. Avoid vague names
  (`helper`, `utils`, `tools`).
- **`description`**: non-empty, ≤ 1024 characters, **third person**, states both *what* the skill
  does and *when* to use it (include concrete trigger terms). The description is the only thing
  pre-loaded for skill selection, so it must carry its weight.
- **Body**: keep SKILL.md under **500 lines**; split detail into `references/` as it grows.
- **References one level deep**: every reference file links directly from SKILL.md. Avoid nested
  references (SKILL.md → a.md → b.md) — Claude may only partially read deeply nested files.
- **Table of contents** for any reference file longer than ~100 lines, so partial reads still see
  the full scope.
- **Forward-slash paths** only (`reference/guide.md`), never backslashes.

## Description quality

Write in third person ("Extracts text from PDFs…", not "I can…" / "You can…"). Be specific and
include the terms a user would mention. Examples:

> `Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when the user mentions PDFs, forms, or document extraction.`

Avoid: `Helps with documents`, `Processes data`, `Does stuff with files`.

## Conciseness and degrees of freedom

- **Concise is key.** Assume Claude is already smart; only add context it doesn't have. Challenge
  every paragraph: does it justify its token cost?
- **Match freedom to fragility.** High freedom (text guidance) when many approaches are valid;
  medium freedom (parameterized scripts/pseudocode) when a pattern is preferred; low freedom
  (exact scripts, "run this, don't modify it") when operations are fragile and order matters.
- **Don't offer too many options.** Give one default with an escape hatch, not a menu.

## Progressive disclosure

SKILL.md is a table of contents that points to detail loaded only when needed. Two patterns:
- **High-level guide + references**: quick start in SKILL.md, advanced topics in `FORMS.md`,
  `REFERENCE.md`, `EXAMPLES.md`.
- **Domain organization**: split reference files by domain (`reference/finance.md`,
  `reference/sales.md`) so unrelated context isn't loaded. Name files descriptively
  (`form_validation_rules.md`, not `doc2.md`).

## Workflows and feedback loops

- For complex multi-step tasks, provide a **checklist** the agent copies and checks off.
- Build **validator → fix → repeat** loops (a script or a STYLE_GUIDE.md as the "validator").
  Insist: "only proceed when validation passes."

## Content guidelines

- **No time-sensitive information** in the main body. Put deprecated material in a collapsed
  "Old patterns" section rather than "before August 2025, do X".
- **Consistent terminology**: pick one term per concept and use it throughout.
- Prefer **concrete examples** (input/output pairs) over abstract description.

## Scripts (skills with executable code)

- **Solve, don't punt**: scripts handle their own error conditions instead of failing into Claude.
- **No voodoo constants**: justify/document every magic number.
- **Provide utility scripts** for deterministic operations (more reliable, token-saving) and make
  execution intent explicit ("Run `analyze.py`" vs "See `analyze.py` for the algorithm").
- **Don't assume packages are installed**; list dependencies and the install command.
- For MCP tools, use fully qualified names (`ServerName:tool_name`).
- **Plan-validate-execute**: for batch/destructive work, emit a plan file, validate it with a
  script, then execute.

## Evaluation

- **Build evaluations first.** Establish a baseline without the skill, write ≥ 3 scenarios that
  test real gaps, then write the minimal instructions to pass them. (This is exactly what the
  factory's gold-standard + rubric + autoresearch loop automates.)
- **Test across models** you plan to run (Haiku/Sonnet/Opus): more detail may be needed for
  smaller models; avoid over-explaining for larger ones.
- Iterate by observing how Claude actually navigates the skill — fix the structure, not just the words.
