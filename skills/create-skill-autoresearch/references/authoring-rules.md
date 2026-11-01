# Authoring Rules

The examples and rationale behind the authoring rules neither companion states. The rules themselves are in the
phase-3.md 3.3 checklist, which every build runs; read this file only when one of them needs its example or reason.
The craft layer is writing-for-agents (fallback:
[standalone-fallback.md](standalone-fallback.md)). Source of the platform rules: Anthropic's
[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices);
where this file and that page disagree, the page wins and this file is updated.

## Contents
- Naming and description
- Freedom and defaults
- Structure
- Workflows
- Content
- Scripts
- Evaluation
- Where the craft passes fire

## Naming and description

- **Measure the description on the joined folded block.** A `description: >-` block spans lines; a one-line
  reader (`grep '^description:'`) sees it as empty and lets an over-limit description through. Read the block up
  to the next top-level key, join it, then count. Count lines over the whole file, as tooling does.
- **No vague names**: not `helper`, `utils`, `tools`. Gerunds (`processing-pdfs`) are fine alone, but the
  naming of the set the skill ships with wins over the gerund default.
- **Description, good and bad.** Good: `Extract text and tables from PDF files, fill forms, merge documents. Use
  when working with PDF files or when the user mentions PDFs, forms, or document extraction.` Bad: `Helps with
  documents`, `Processes data`, `Does stuff with files`.

## Freedom and defaults

- **Match freedom to fragility.** Text guidance where many approaches are valid; a parameterised script or
  pseudocode where one pattern is preferred; an exact script ("run this, do not modify it") where the operation
  is fragile or order matters.
- **One default, with an escape hatch** — never a menu of equal options for the agent to choose from.

## Structure

- A pointer between two references that SKILL.md both links is lateral and fine; what "one level deep" forbids
  is a file reachable only through another reference.
- Name reference files by content (`form_validation_rules.md`, not `doc2.md`); split by domain
  (`references/finance.md`, `references/sales.md`) when branches need different material.

## Workflows

- Give a multi-step task a **checklist** the agent copies and ticks off.
- Build **validator → fix → repeat** loops (a script, or a style guide as the validator), and state the gate:
  proceed only when validation passes.
- **Plan, validate, execute** for batch or destructive work: write a plan file, check it with a script, then run
  it.

## Content

- **No time-sensitive statements** in the body ("before August 2025, do X"). Superseded material goes in a
  collapsed "Old patterns" section.

## Scripts

- **Solve, don't punt**: a script handles its own error conditions instead of failing back to the agent.
- **No voodoo constants**: every magic number carries its reason in a comment.
- State **execution intent**: "Run `analyze.py`" (execute it) vs "See `analyze.py` for the algorithm" (read it).
- List each dependency **with its install command**; never assume a package is installed.
- Name MCP tools **fully qualified** (`ServerName:tool_name`).

## Evaluation

- **Evaluations before instructions**: a baseline without the skill, at least 3 scenarios that hit real gaps, then
  the least text that passes them. The factory fixes its evaluations before the draft: the Phase 1 gold
  standards (3 or more) are the scenarios and the confirmed 2.3 rubric scores them, both settled before 3.3
  writes a line; 3.4-3.6 then turn them into scripts, and 3.5 measures the draft against them.
- **Test on every model the skill targets**: a smaller model may need more detail, a larger one less.
- Watch how the agent actually navigates the skill (its transcripts, not just its outputs) and fix the structure,
  not only the wording.

## Where the craft passes fire

Invocation choice: 1.1-1.5 and 3.1. Information hierarchy and leading-word candidates: 3.1. Description craft:
the 3.3 checklist. Pruning: before the 3.5 baseline and as standing Phase 4 experiments (4.3). The five failure
modes: probed in the 5.1 premortem and hunted by the 5.2 Devil's Advocate.
