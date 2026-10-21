# Standalone Fallback

What the factory needs when its companions are missing. With **skill-creator** installed, its
writing guide owns authoring; with **writing-for-agents** installed, it owns the craft passes. Read
this file only for the parts whose companion is absent.

Install the companions instead of relying on this file:

```bash
npx skills add https://github.com/anthropics/skills --skill skill-creator
npx skills add mattpocock/skills --skill writing-for-agents
```

## Hard constraints

Their single source is the pre-flight checklist in phase-3.md 3.3: the platform rules, plus `license` and the
version as `metadata: {version}` (a top-level `version` only where a registry reads it). They apply with or
without companions, and the self-test and the Phase 5 panel assert them. The authoring rules no companion states are in
[authoring-rules.md](authoring-rules.md), which 3.3 reads on every build; this file holds only what the
companions would otherwise supply.

## Invocation

- **Model-invoked** keeps the description in context every turn (context load) so the agent — or
  another skill — can fire it. Its description lists one trigger per distinct branch, leading word
  first, no synonym padding.
- **User-invoked** (`disable-model-invocation: true`) costs no context but the human must remember
  it. Its description is a one-line human-facing summary with no trigger list.

Pick model-invoked only when the agent or another skill must reach it unprompted.

## Craft passes

Run before the Phase 3 baseline and as standing Phase 4 experiments:

1. **Completion criteria** — every step ends on a checkable, and where it matters exhaustive,
   criterion ("every modified file accounted for", not "list the changes").
2. **Leading words** — collapse a restated quality or a fuzzy gate into one pretrained word the
   model already thinks with (*tight*, *red*, *relentless*).
3. **Pruning** — one source of truth per meaning; delete any sentence that does not change
   behaviour versus the model's default; delete stale lines.
4. **Disclosure** — inline what every branch needs; push behind a pointer what only some branches
   reach; sharpen the pointer's wording before inlining material back.

## The five failure modes

The premortem (5.1) probes each one; the Devil's Advocate (5.2) hunts them.

| Failure mode | Symptom | Cure |
|--------------|---------|------|
| Premature completion | A step ends before it is done | Sharpen the completion criterion |
| Duplication | One meaning in several places | Collapse to a single source of truth |
| Sediment | Stale layers nobody dares remove | Line-by-line relevance check |
| Sprawl | Too long even with every line live | Disclose behind pointers; split by branch |
| No-op | A line the model already obeys | Delete it, or use a stronger leading word |
