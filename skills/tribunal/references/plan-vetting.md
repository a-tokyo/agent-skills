# Plan Vetting — the pre-implementation gate

Same machinery as the delivery panel, earlier timing. The delivery panel asks "did we build
it right?"; the vetting gate asks "are we about to build the right thing, verifiably?" —
and freezes the acceptance criteria the delivery panel later audits against. The two gates
are complementary: vetting is cheap insurance against expensive wrong directions; delivery
verification is insurance against unfaithful execution.

## Contents

- [When to invoke](#when-to-invoke)
- [The three perspectives](#the-three-perspectives)
- [The 2/3 rule and the single refinement pass](#the-23-rule-and-the-single-refinement-pass)
- [Criteria before implementation](#criteria-before-implementation)
- [Bounds](#bounds)
- [Cheaper variant: Proposer + Critic](#cheaper-variant-proposer--critic)
- [Costlier variant: N candidates + judge](#costlier-variant-n-candidates--judge)
- [Relationship to the delivery panel](#relationship-to-the-delivery-panel)

## When to invoke

Vet any plan above the triviality threshold: multi-slice work, hard-to-reverse decisions
(architecture, migrations, deployments), or anything where a wrong direction wastes more
than the gate costs. Skip it for single-step tasks a verification command already covers.

## The three perspectives

Three independent reviewers, each seeing ONLY its own lens (a single reviewer asked all
three questions averages them out). Each returns APPROVE or REJECT plus a critique.

| Perspective | One-line gloss | Focus questions |
|---|---|---|
| Rigor | Will it work? | Is the data/work flow correct? Is each slice's verification criterion specific enough to actually test the objective (not just artifact existence)? Does the plan respect the system's real constraints? |
| Ergonomics | Can it be followed? | Is each step unambiguous for the executing agent? Are slice names and boundaries clear? Is the experience of the deliverable's consumer prioritized? |
| Pragmatism | Is it worth it? | Is this the fastest sufficient path? Where is it over-complicated or gold-plated? Is the token/time budget proportionate to the stakes? |

## The 2/3 rule and the single refinement pass

- 3/3 or 2/3 APPROVE: the plan proceeds as-is; attach any minority critique as advisory
  notes.
- 1/3 or 0/3 APPROVE: merge all rejecting critiques into ONE consolidated critique, hand it
  to the plan author for exactly ONE refinement pass, then re-vet once.
- Still failing after the refinement pass: escalate to the human with the merged critique.

The bound is the point. Vetting is a gate with one retry, not a negotiation — unbounded
pre-implementation polishing burns the budget the execution needs, and runtime escalation
(the doer's BLOCKED path) is the safety net for what vetting misses.

## Criteria before implementation

Every slice of an approved plan must carry, written into the plan itself before any
implementation:

1. Acceptance criteria — checkable assertions, not aspirations.
2. The exact verification commands (or observations, for non-code deliverables) that must
   pass.
3. Optionally, a Devil's Advocate attack surface: slice-specific adversarial angles seeded
   for the delivery panel.

The Rigor perspective explicitly audits criteria quality: does each check test the stated
objective, or merely that something exists? Once vetting approves, the criteria are FROZEN —
they become the rubric the delivery panel scores against. Neither the doer nor the panel may
quietly move the bar; criteria changes go back through the gate.

## Bounds

- **Decomposition depth**: maximum 4 levels of slicing. At depth 3, force a reconsideration:
  before slicing any deeper, the planner must state in one sentence why the current level
  cannot be executed as-is, then deliberately confirm the extra level in a second pass.
- **Complexity-weighted retry budgets**: default 5 doer attempts per slice, with the failure
  reason fed back on each retry. Weight the budget by complexity: complex slices get more
  attempts, trivial slices fewer. Spend iteration where the uncertainty lives. This budget
  counts doer dispatches and is distinct from the delivery panel's cap of 3 panel rounds per
  slice; whichever budget exhausts first escalates the slice to the human.
- **Plans are graphs, not trees**: slices may have cross-dependencies; each slice declares
  expected inputs and outputs, forming contracts the Rigor perspective can check.
- **Mechanical checks stay mechanical**: structural validity (schemas, references, links) is
  verified deterministically outside the panel; reviewers spend judgment only on judgment.

## Cheaper variant: Proposer + Critic

For small and medium plans, replace the 3-perspective panel with a two-agent loop:

1. The Proposer generates the plan.
2. The Critic evaluates it independently and APPROVEs or REJECTs with a critique.
3. On rejection, the Proposer revises with the critique attached and the Critic re-evaluates.
4. The Critic is bounded too: after a third consecutive rejection, force a recalibration —
   the Critic must either name the single concrete requirement no proposal has yet met, or
   accept the next proposal that meets the requirements it has already named.

Bounding the critic is the distinctive rule: most adversarial designs only bound the
proposer, which lets a perfectionist reviewer stall delivery indefinitely. Both sides carry
a budget.

## Costlier variant: N candidates + judge

When the approach itself is in doubt, generate diversity before vetting quality:

1. Spawn N candidate plans in parallel — independent agents, identical brief.
2. A judge (preferably a different model family than the generators, to avoid
   self-preference bias) scores all candidates and selects the best.
3. The winner proceeds to the normal vetting gate.

Cost: wall-time of the slowest candidate, but roughly N times the tokens. Justified for
high-stakes or hard-to-reverse plans and genuinely ambiguous decompositions; wasteful for
routine plans and narrow solution spaces. Rule of thumb: parallel candidates buy diversity
of approach; the vetting panel buys quality of a chosen approach.

## Relationship to the delivery panel

| | Vetting gate (pre) | Delivery panel (post) |
|---|---|---|
| Object reviewed | The plan / approach | The delivered work |
| Reviewers | Rigor / Ergonomics / Pragmatism | Verifier-A (Quality) / Verifier-B (Utility) / Devil's Advocate |
| Decision rule | 2/3 approval | Consensus / majority with synthesis |
| On failure | Merged critiques -> ONE refinement pass | Fix list -> fresh doer -> re-verify (max 3 panel rounds) |
| Escape hatch | Bounded depth + forced reconsideration | Structured surrender envelope + human escalation package |
| Blind spot | Execution drift, environment surprises | Wasted work on a doomed approach |

The thread tying them together: vetting freezes the criteria; the delivery panel audits
against exactly those criteria. A full tribunal run is vet -> execute with per-slice
criteria -> verify delivery against the criteria frozen at vetting time.
