---
name: tribunal
version: 0.0.1
license: MIT
description: >-
  Runs the doer -> verifier-panel -> consensus pattern to verify a deliverable before it
  ships. An orchestrator slices the work, freezes acceptance criteria before implementation,
  spawns a doer, then convenes a context-walled verifier panel - Verifier-A (Quality),
  Verifier-B (Utility), and a Devil's Advocate - for independent review with
  evidence-anchored scoring, a single anonymized synthesis round, and evidence-adjudicated
  consensus ending in a SHIP / SHIP_WITH_CAVEATS / ITERATE / BLOCK / ESCALATE verdict logged
  to a gate ledger. Use for multi-agent verification of any artifact - code slices, plans,
  documents, audits - whenever asked to verify a deliverable, vet a plan, run a consensus
  review or independent review, set up a doer-verifier loop, or gate a ship decision. Works
  on any platform with parallel subagents; degrades to sequential fresh-context sessions
  without them. Not for trivial single-file edits or ordinary code review.
---

# Tribunal

Deliver work through three walls: a doer who builds, a panel that judges
independently, and an orchestrator that adjudicates on evidence. Announce at
start: "Running this through the tribunal pattern: doer -> verifier panel -> consensus."

## Roles

| Role | Mandate | Bias |
|---|---|---|
| Orchestrator | Slices work, freezes criteria, spawns roles, adjudicates consensus, owns the ledger. Produces no deliverable content itself; never trusts a report it has not checked against the artifact. | Evidence over testimony |
| Doer | Implements one slice exactly per spec; runs the slice's verification commands; reports status plus evidence. | Wants to ship |
| Verifier-A (Quality) | Spec compliance first — missing / extra / misunderstood — then correctness, completeness, clarity. | Neutral |
| Verifier-B (Utility) | Practical value: real-world usability, edge cases, integration, consumer experience. | Neutral |
| Devil's Advocate | Builds the strongest case against shipping; names exact failure scenarios. | Explicitly negative |

## When to use

Use for multi-part or high-stakes deliverables: code slices, build plans,
design documents, audits, migrations — anywhere a shipped defect costs more
than a panel. Do NOT use for trivial single-file edits, typo fixes, or work a
single verification command already proves. This is not ordinary code review:
code review is one reviewer improving a change in dialogue; tribunal is three
independent measurements of a frozen artifact against pre-declared criteria,
adjudicated to a ship decision. Do not nest tribunals: a panel-produced
artifact (verdict, ledger, plan) is verified as an ordinary deliverable by a
fresh panel; no role ever runs the protocol on its own output.

## Lifecycle

```
plan -> [vet plan] -> per slice:
  criteria -> doer -> [premortem] -> 3 verifiers (parallel) -> consensus
     ^                                                            |
     +------------- ITERATE (max 3 panel rounds) ----------------+
                                                                  v
                                       verdict -> ledger entry -> next slice
```

1. Slice the deliverable into independently gate-able units.
2. Write each slice's acceptance criteria and verification commands BEFORE
   implementation — they double as the panel's rubric. Derive 4-8 named
   dimensions, weights (equal by default), and a target (default 0.80).
3. Spawn a fresh doer with the slice spec only: full task text pasted (never
   "read the plan file"), scene-setting context, an ask-questions-now gate.
   Template: [role-prompts.md](references/role-prompts.md).
4. Doer implements, runs the slice's verification commands, and reports a
   diff summary, verbatim command output, and exactly one status (below).
5. Optional premortem: list likely failure modes for this slice; seed them to
   the panel as standing risks.
6. On DONE: inspect the actual diff yourself, then spawn all 3 verifiers in
   parallel, context-walled, in a single dispatch.
7. Run the consensus protocol; record the verdict in the gate ledger.
8. SHIP -> next slice. ITERATE -> merge findings into a fix list -> fresh
   doer with findings attached. Max 3 panel rounds per slice, then ESCALATE
   to the human. Re-panels: fresh verifiers, one parallel dispatch, each
   re-scoring every dimension with the prior disputed findings attached as
   flagged risks (targeted re-tests rubber-stamp partial fixes).

Verify-only entry: when the artifact already exists (audit or review-only),
write the acceptance criteria from the original request — never reverse-
engineered from the artifact — freeze them, then enter at the panel step (6).

| Doer status | Orchestrator handling |
|---|---|
| DONE | Verify the diff exists and the commands actually ran; proceed to panel. |
| DONE_WITH_CONCERNS | Read concerns first. Correctness or scope concerns: address before the panel. Mere observations: log and proceed. |
| NEEDS_CONTEXT | Supply the missing context; re-dispatch the same model. The problem was input, not capability. |
| BLOCKED | Triage in order: (1) more context, (2) a more capable model, (3) decompose the slice, (4) escalate to the human. |

The doer-dispatch budget (default 5 per slice, complexity-weighted) is
separate from the max 3 panel rounds; whichever exhausts first escalates.
Never retry the same model unchanged — if the doer is stuck, change at least
one of context, model, or task size. Never ignore an escalation.

## Context wall (non-negotiable)

Independent vantage points triangulate ground truth; the wall keeps the
measurements independent. Each verifier RECEIVES exactly:

1. The slice's acceptance criteria (frozen before implementation)
2. The artifact: the diff plus paths of any new files to read (non-code
   deliverables: the new version plus its predecessor, if one exists)
3. Reference materials for the slice (conventions, trusted exemplars)
4. Permission to run the slice's verification commands themselves
5. Premortem risks, when available

Verifiers NEVER RECEIVE:

- The doer's conversation, reasoning, drafts, or self-assessment
- The plan's design rationale or the orchestrator's history
- Each other's scores or identities (round 1 is fully independent)
- Expected scores, prior-round scores, or any hint of either

## Evidence-anchored scoring

Verifiers score each criterion dimension 1-10 with a confidence (0.0-1.0),
one dimension per evaluation pass — never a single holistic score.

- Score 8+ — must cite at least one verbatim quote from the artifact.
- Score 3 or below — must cite the failing file:line or command output.
- A finding without a file:line, quote, or output excerpt is discarded.

Doer-side iron law: no completion claim without fresh verification output —
"should pass" is not evidence; the orchestrator re-checks against the diff.

## Consensus protocol (summary)

Full mechanics: [consensus-protocol.md](references/consensus-protocol.md).
End-to-end example: [worked-example.md](references/worked-example.md).

1. Collect 3 independent scorecards. Spot-verify the cited evidence behind
   outlier scores: a score whose evidence is factually refuted (quote does
   not exist, claim contradicts the artifact) is excluded and logged as
   refuted dissent. Adjudicate on evidence; never average disagreement away.
2. Disagreement triggers (any one fires a synthesis round): score spread of 2
   or more on a dimension; pass/fail split (one member's weighted overall
   meets the target while another's falls more than 0.15 below target); the
   Devil's Advocate scores any dimension at 1; confidence below 0.5 against
   confidence above 0.8 on the same dimension.
3. One synthesis round only: rationales harvested from the scorecards (max
   500 words, disputed dimensions only), anonymized and shuffled, shared
   simultaneously; revise with justification or maintain with a rebuttal.
4. Resolution: converged (all within 1) — mean of the 3 scores; majority
   (2-of-3 within 1) — mean of the agreeing pair, dissent logged; deadlock —
   ESCALATE to the human.
5. The Devil's Advocate may write `ESCALATE: <reason>`. An unrebutted
   correctness or safety scenario is never silently overruled — stop and
   surface it, even when the majority disagrees.

## Verdicts

| Verdict | Meaning | Trigger |
|---|---|---|
| SHIP | Slice passes; merge and move on. | Overall at or above target; no dimension blocked. |
| SHIP_WITH_CAVEATS | During a build, treat as an ITERATE trigger: caveats become the fix list — each caveat is fixed and re-verified, or logged as deferred with a reason. Never a final state unless the human explicitly accepts the logged caveats. | Overall at or above target minus 0.10 (but below target); only non-blocking caveats (defined beside the verdict table in consensus-protocol.md). |
| ITERATE | Findings become a fix list for a fresh doer. | Overall below the caveat band, or in the caveat band with a surviving blocking caveat. Rework overrides passing math only via the BLOCK rule or a verified (unrefuted) failure scenario from any panel member — otherwise the math decides. |
| BLOCK | Stop work on the slice until the blocking concern is addressed. | A majority scores any dimension 3 or below, regardless of overall. |
| ESCALATE | Surface to the human with full context. | Deadlock, an unrebutted DA scenario, or max 3 panel rounds exhausted. |

Verifiers recommend among the first four; ESCALATE is the orchestrator's
verdict (or the Devil's Advocate's signal), never a panel recommendation.

## Gate ledger

Append to `.tribunal-gates.md` (gitignored; outside a VCS, any scratch file kept out
of the deliverable), or your repo's verification-docs convention, per slice:

```
## Slice <id> - <title>
- Round <n>: A=<rec> B=<rec> DA=<rec> -> <verdict>
- Dissents: <role>: <dimension> <score> vs consensus <score> - <one-line
  rationale> (status: logged | refuted)
- Caveats: <each: fixed | deferred - <reason>>
- Panel rounds: <n> of 3 (doer retries are a separate budget)
```

Caveats are never silently dropped. An ephemeral ledger gets cleaned up
before handover — but only after its open caveats and surviving dissents
move to a durable record (decision log, follow-ups list, or final report).

## Model policy

- Doers and verifiers inherit the session's default model.
- NEVER downgrade the Devil's Advocate below the session default.
- Mechanical sub-tasks outside tribunal roles (lint fixes, file moves) may
  use a cheaper model — every cheap-doer output still gets verified.
- Prefer a different model family for the panel — uncorrelated blind spots.
- A BLOCKED doer that needs deeper reasoning climbs to a more capable model.

## Plan vetting (pre-implementation gate)

Same machinery, earlier timing: vetting freezes the criteria the panel later
audits. Before executing a non-trivial plan, run it past three independent
perspectives — Rigor (will it work; are the checks specific?), Ergonomics
(can the doer actually follow it?), Pragmatism (is it the cheapest
sufficient path?). 2-of-3 approval proceeds; otherwise merge the critiques
into exactly ONE refinement pass, re-vet once, then escalate if still
failing. Full procedure: [plan-vetting.md](references/plan-vetting.md).

## Platforms

Works on any agent platform with parallel subagents: spawn the panel as
three parallel calls in one dispatch. Without subagents, run each role as a
sequential fresh-context session in the same order (doer -> Verifier-A ->
Verifier-B -> Devil's Advocate -> consensus), enforcing the context wall by
prompt discipline: give each verifier only the RECEIVES list above, never
the doer's output reasoning. Run synthesis sequentially: collect all
rationales first, then present the full anonymized set to each fresh session
in turn — the simultaneity that matters is informational, not wall-clock. If
a single session must play every role, delimit each role switch explicitly
and forbid the verifier role from using memory of authorship — only the
pasted criteria and the artifact itself. Single-session mode degrades the
independence guarantee: label the ledger entry and verdict "single-context
verdict (reduced independence)" and prefer the sequential fresh-session mode
whenever the platform allows.

## Red flags

- Quality review before spec compliance — polish on the wrong thing.
- Three quick SHIPs with no quoted evidence — a rubber-stamp panel.
- Findings without file:line, quote, or output — discard them.
- Devil's Advocate agreeing by round 2 without exhausted attack vectors.
- A verifier that has seen the doer's reasoning — wall breach; respawn fresh.
- Averaging a 3-vs-8 disagreement to 5.5 instead of adjudicating evidence.

Full catalogue: [anti-patterns.md](references/anti-patterns.md).
