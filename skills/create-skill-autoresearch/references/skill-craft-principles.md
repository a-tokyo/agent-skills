# Skill-Craft Principles

The craft layer every skill this factory produces must satisfy: how a skill stays **predictable**,
beyond the hard platform rules in [skill-authoring-best-practices.md](skill-authoring-best-practices.md)
(the compliance layer — when the two overlap, that file's Anthropic-sourced rules win).
Distilled from `writing-great-skills` by Matt Pocock
([mattpocock/skills](https://github.com/mattpocock/skills), MIT); in this repo the full source is
vendored at `.agents/skills/writing-great-skills/` for contributors. Terminology (predictability,
leading word, no-op, sediment, sprawl) follows its glossary.

## Contents
- Predictability — the root virtue
- Where each pass fires in the pipeline
- Invocation axis
- Description writing
- Information hierarchy
- Leading words
- Pruning pass
- The five failure modes

## Predictability — the root virtue

A skill exists to wrangle determinism out of a stochastic system. Predictability means the agent
takes the same **process** every run — not that it produces the same output (a brainstorming skill
should predictably diverge). Every rule below is a lever on it, and it is what the factory's
`predictability` rubric dimension scores.

## Where each pass fires in the pipeline

| Section below | Fires at |
|---------------|----------|
| Invocation axis | Phase 1.4 (interview question) and 3.1 (DESIGN.md) |
| Description writing | Phase 3.3 (pre-flight checklist) |
| Information hierarchy | Phase 3.1 (DESIGN.md structure plan) |
| Leading words | Phase 3.1 (candidates) and Phase 4 (hunt experiments) |
| Pruning pass | Phase 3.3 (before baseline) and Phase 4 (standing experiment class) |
| Five failure modes | Phase 5.1 (premortem probes) and 5.2 (Devil's Advocate) |

## Invocation axis

Two ways a skill is reached, trading different costs:

- **Model-invoked** — the skill keeps its `description`, so the agent can fire it autonomously and
  other skills can reach it (the human can still type its name). Pays permanent **context load**:
  the description sits in the window every turn.
- **User-invoked** — `disable-model-invocation: true` strips the description from the agent's
  reach; only the human typing its name fires it, and no other skill can. Zero context load, but it
  spends **cognitive load**: the human is the index that must remember it exists. Its `description`
  becomes human-facing — a one-line summary, trigger lists stripped.

Decision rule: pick model-invocation only when the agent must reach the skill on its own, or
another skill must. If it only ever fires by hand, make it user-invoked and pay no context load.

Phase 1.4 interview wording: *"Should the agent fire this skill on its own when it detects the
need (model-invoked — its description then occupies context every turn), or only when you type its
name (user-invoked — zero context cost, but you must remember it exists)?"* Record the answer as
`INVOCATION_MODE`; it drives the description style in 3.3.

## Description writing

A model-invoked description does two jobs — state what the skill is, and list the distinct
**branches** (ways of being invoked) that should trigger it. Every word is permanent context load,
so it earns harder pruning than the body:

- **Front-load the leading word** — the description is where it does its invocation work.
- **One trigger per branch.** Synonyms that rename a single branch are duplication ("build features
  using TDD … asks for test-first development" is one branch written twice). Collapse them; keep
  only genuinely distinct branches.
- **Cut identity that's already in the body** — keep the description to triggers, plus any
  "when another skill needs…" reach clause.
- A user-invoked description is a one-line human-facing summary, trigger lists stripped.

## Information hierarchy

A skill's content is a ladder ranked by how immediately the agent needs it:

1. **In-skill step** — an ordered action in SKILL.md, the primary tier. Every step ends on a
   **completion criterion** that is *checkable* (the agent can tell done from not-done) and, where
   it matters, *exhaustive* ("every modified model accounted for", not "produce a change list").
   A vague criterion invites premature completion; a demanding one drives thorough legwork — and
   the demand axis binds flat reference too ("every rule applied").
2. **In-skill reference** — definitions, rules, facts consulted on demand. A flat peer-set (every
   rule of a review on one rung) is a fine arrangement, not a smell.
3. **Disclosed reference** — pushed into a linked file, reached by a context pointer, loaded only
   when the pointer fires.

Disclosure is licensed by **branching**: inline what every branch needs; push behind a pointer what
only some branches reach. The pointer's *wording*, not its target, decides when and how reliably
the agent reaches the material — sharpen the wording before inlining the material back.
**Co-locate** within a file: a concept's definition, rules, and caveats under one heading, not
scattered, so reading one part brings its neighbours with it.

## Leading words

A **leading word** is a compact concept already in the model's pretraining that the agent thinks
with while running the skill (*lesson*, *fog of war*, *tracer bullets*, *tight*, *red*). It anchors
behaviour in the fewest tokens by recruiting priors the model already holds — in the body it
anchors execution (same behaviour every time the word appears), in the description it anchors
invocation (shared language between prompts and skill fires it more reliably).

Hunt procedure, per draft: find a quality restated across a passage ("fast, deterministic,
low-overhead") or a fuzzy gate ("a loop you believe in") and collapse it into one pretrained token
(a *tight* loop; the loop goes *red*). Fewer tokens and a sharper hook. A leading word too weak to
beat the model's default (*be thorough* when the agent is already thorough-ish) is a no-op — the
fix is a stronger word (*relentless*), not a different technique. Prefer existing words: a coined
word recruits no priors and costs definition tokens.

## Pruning pass

Run on every draft before measuring the baseline, and keep it as a standing Phase-4 experiment
class:

1. **Single source of truth** — each meaning lives in exactly one authoritative place; changing the
   behaviour is a one-place edit.
2. **Relevance per line** — does the line still bear on what the skill does, or has it gone stale?
3. **No-op test per sentence** — does this sentence change behaviour versus what the model does by
   default? When a sentence fails, delete the whole sentence rather than trim words from it. Be
   aggressive: most prose that fails should go, not be rewritten.

## The five failure modes

The premortem (5.1) probes each of these as a required scenario; the Devil's Advocate (5.2) hunts
them in the artifact.

| Failure mode | Symptom | Cure (in order) |
|--------------|---------|-----------------|
| Premature completion | A step ends before it's genuinely done; attention slips to *being done* | Sharpen the completion criterion first (cheap, local); only if it is irreducibly fuzzy *and* the rush is observed, hide the post-completion steps by splitting the sequence |
| Duplication | The same meaning in more than one place | Collapse to a single source of truth; if attention on the idea is the goal, repeat a leading-word *token*, never the meaning |
| Sediment | Stale layers settle because adding feels safe and removing feels risky | The relevance check, applied line by line — the default fate of any skill without a pruning discipline |
| Sprawl | The skill is simply too long even with every line live and unique | The ladder: disclose reference behind pointers; split by branch or sequence so each path carries only what it needs |
| No-op | A line the model already obeys by default — load spent to say nothing | The sentence-level no-op test; for a weak leading word, a stronger word, not a different technique |
