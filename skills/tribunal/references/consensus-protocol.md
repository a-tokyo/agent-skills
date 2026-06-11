# Consensus Protocol — full mechanics

The complete doer -> verifier-panel -> consensus procedure. The SKILL.md body carries the
summary; this file carries every definition, threshold, and step the orchestrator needs to run
a contested round without improvising.

## Contents

- [Protocol overview (4 phases)](#protocol-overview-4-phases)
- [Panel composition](#panel-composition)
- [Scoring rules](#scoring-rules)
- [Confidence semantics](#confidence-semantics)
- [Disagreement triggers (exact definitions)](#disagreement-triggers-exact-definitions)
- [Synthesis round mechanics](#synthesis-round-mechanics)
- [Resolution math](#resolution-math)
- [Evidence-based adjudication](#evidence-based-adjudication)
- [Devil's Advocate behavioral contract](#devils-advocate-behavioral-contract)
- [Minority-report persistence](#minority-report-persistence)
- [Orchestrator checklist](#orchestrator-checklist)
- [Research basis](#research-basis)

## Protocol overview (4 phases)

```
Phase 1: Independent scoring
  3 panel members score in isolation. No cross-talk.
  Each dimension scored atomically (separate evaluation pass).

Phase 2: Agreement check + adjudication
  Orchestrator spot-verifies evidence behind outlier scores (see Adjudication).
  All dimensions within 1 point spread -> consensus reached.
  Any disagreement trigger fires -> synthesis round.

Phase 3: Synthesis round (at most one)
  Members write rationales on disputed dimensions only.
  Rationales anonymized, shuffled, shared simultaneously.
  Members revise with justification or maintain with a rebuttal.

Phase 4: Resolution
  Converged (all within 1 point) -> mean of 3.
  Majority (2-of-3 within 1 point) -> mean of the agreeing pair, dissent logged.
  Deadlock (no pair within 1 point on some dimension) -> ESCALATE to the human.
```

## Panel composition

Exactly three members. More members give diminishing returns for a bounded artifact; fewer
lose the triangulation.

| Role | Focus | Bias |
|---|---|---|
| Verifier-A (Quality) | Spec compliance first (missing / extra / misunderstood requirements), then correctness, completeness, clarity | Neutral |
| Verifier-B (Utility) | Real-world usability, edge cases, integration quality, consumer experience | Neutral |
| Devil's Advocate | Failure modes, hidden assumptions, missing constraints, drift risk, over-engineering | Explicitly negative |

All members score all dimensions; A and B each treat their focus areas as primary. Spawn all
three in parallel in a single dispatch. Each receives only the context-wall RECEIVES list from
the SKILL.md body — never the doer's reasoning, never each other's scores, never expected or
prior scores.

Verifiers may re-run the slice's verification commands, but concurrent runs in one shared
working tree can collide (ports, caches, fixtures) and produce genuine-looking failure output
that adjudication cannot refute. Give each verifier an isolated workspace copy, or have the
orchestrator serialize the command runs and share the verbatim output.

Dimensions come from the slice's acceptance criteria. If the criteria are prose, derive 4-8
named dimensions from them before dispatching the panel (e.g. correctness, completeness,
robustness, clarity) and give every verifier the same list with the same weights. Equal weights
unless the criteria say otherwise; move to unequal weights only when the criteria explicitly
rank one property above another (e.g. "correctness is mandatory, polish is optional"), and
record the chosen weights in the ledger so re-panels score against the same rubric.

## Scoring rules

- Scale: 1-10 per dimension. Score each dimension in its own evaluation pass; a single
  holistic score is forbidden (halo inflation).
- Score 8 or higher: must cite at least one verbatim quote from the artifact as evidence.
- Score 3 or lower: must cite the specific file:line, section, or command output where the
  failure occurs.
- Scores 4-7: evidence recommended, not required.
- Any finding (in scores or prose) without a file:line, verbatim quote, or output excerpt is
  discarded by the orchestrator before consensus.
- General rule: the more extreme the score in either direction, the harder the evidence
  requirement.

## Confidence semantics

Each score carries a confidence from 0.0 to 1.0:

| Band | Meaning |
|---|---|
| 0.9-1.0 | Very confident, clear evidence |
| 0.7-0.8 | Confident, minor ambiguity |
| 0.5-0.6 | Uncertain, could go either way |
| below 0.5 | Low confidence, limited evidence |

Confidence is a reported signal, used for exactly two things:

1. Disagreement detection — trigger 4 below.
2. Tie-breaking context when the orchestrator must weigh equally evidenced positions.

Confidence is NEVER a score multiplier. Consensus math is the plain arithmetic mean of the
converging scores. Do not weight scores by confidence.

## Disagreement triggers (exact definitions)

A synthesis round fires when ANY of these holds. Triggers 1, 3, and 4 are per-dimension;
trigger 2 is on the weighted overall. The disputed set is the union of every flagged dimension.

1. **Spread**: score spread of 2 or more points on any single dimension across members.
2. **Pass/fail split**: one member's weighted overall meets or exceeds the target score while
   another member's falls more than 0.15 (normalized) below the target — the members disagree
   about the verdict, not just a number. Trigger 2 adds to the disputed set only the dimensions on
   which the split members differ by 2 or more points (the within-1 convergence floor means
   smaller gaps are already settled). With integer 1-10 scores the overall gap implies such
   a dimension somewhere, so trigger 2 co-fires with trigger 1 and its disputed set is a
   subset of trigger 1's; it is kept as the verdict-direction check (it matters under
   non-integer or rescaled scoring).
3. **DA veto**: the Devil's Advocate scores any dimension at 1 (the scale minimum). Automatic.
4. **Confidence divergence**: any member marks confidence below 0.5 on a dimension where
   another marks above 0.8.

No trigger fires and adjudication finds nothing refuted: consensus is reached; compute the
final score and skip Phase 3.

## Synthesis round mechanics

Exactly one round. Never a second. More rounds converge to the most verbose member, not the
most correct one.

1. Each member's rationale is harvested from its scorecard — the evidence and justifications
   on the disputed dimensions ONLY (max 500 words; no separate rationale dispatch).
2. The orchestrator anonymizes the rationales — strip role labels, relabel as Rationale-1,
   Rationale-2, Rationale-3 — and shuffles their order before sharing. Members must evaluate
   arguments, not sources.
3. All members receive all rationales simultaneously and respond in parallel.
4. Each member, per disputed dimension, must either:
   - REVISE the score, with a written justification for the change, or
   - MAINTAIN the score, with a one-sentence rebuttal to the strongest opposing argument.
   Silence is not an option; undisputed dimensions are frozen.

A member whose score was excluded as refuted sits the synthesis round out for that
dimension: its rationale is not shared and it does not revise. If exclusions resolve every
flagged dimension (re-run the triggers on the surviving scores), skip synthesis entirely.

Synthesis prompt template: see the role-prompts reference file.

## Resolution math

After synthesis (or directly after Phase 2 when nothing fired):

- **Converged** — every dimension within a 1-point spread: dimension consensus = mean of all
  3 scores.
- **Majority** — some pair of 2 scores within 1 point of each other on each disputed
  dimension: dimension consensus = mean of the 2 agreeing scores. The dissenting score is
  excluded from the math and preserved as a minority report. Tie-break: when more than one
  pair qualifies (e.g. scores 4, 5, 6), the dimension consensus is the median score, and the
  member furthest from the median is the logged dissenter. When the outer scores sit
  symmetric around the median (4/5/6 — both equidistant), consensus is still the median; the
  dissenter is the member whose recommendation diverges from the majority recommendation,
  and if that still does not break the tie, log both outer members as dissents.
- **Deadlock** — on any dimension, no pair within 1 point: no automatic resolution. ESCALATE
  to the human with all three scorecards and rationales.

Weighted overall:

```
overall = sum over dimensions of (dimension_consensus / 10 * weight)
```

Verdict thresholds (target supplied by the caller; default 0.80 normalized):

| Condition | Verdict |
|---|---|
| overall >= target AND no dimension blocked | SHIP |
| overall >= target - 0.10 (but below target), only non-blocking caveats | SHIP_WITH_CAVEATS (iterate trigger during a build; never a final state unless the human explicitly accepts the logged caveats) |
| overall < target - 0.10, or overall in the caveat band with any surviving blocking caveat | ITERATE |
| any dimension scored 3 or below by a majority | BLOCK, regardless of overall |
| deadlock, unrebutted DA scenario, or max 3 panel rounds exhausted | ESCALATE |

Two terms the table relies on: a **blocked dimension** is a dimension a MAJORITY of surviving
members scored 3 or below — a single non-majority low score is a logged dissent, not a block
(this is the same rule as the BLOCK verdict row). A **non-blocking caveat** is a caveat that
does not assert a correctness or safety defect and that carries a concrete fix or an
accepted-and-logged deferral.

ITERATE feedback packet to the fresh doer: top concern per member, the Devil's Advocate's
failure scenarios, per-dimension scores with justifications, and the explicit fix list.

## Evidence-based adjudication

Run this BEFORE consensus math, whenever any disagreement trigger fires or any score is an
outlier (more than 2 from both peers).

1. Take the outlier's cited evidence claim by claim.
2. Verify each claim directly against the artifact: does the quoted text exist? Does the
   artifact actually behave as claimed? Re-run the verification commands in a clean
   workspace if needed (a failure that only reproduces in a dirty tree is environmental).
3. A claim is REFUTED only when it fails factual verification — the quote does not exist, the
   file:line does not show the failure, or the claim contradicts what the artifact
   demonstrably does. "I disagree with the judgment" is not refutation.
4. If every load-bearing claim behind a score is refuted: exclude that score from consensus
   math and log it in the ledger as refuted dissent (position preserved verbatim).
5. If any claim survives: the score stands and proceeds through synthesis normally. Harvest
   surviving observations as iterate items even when the overall verdict passes.
6. Never exclude a score merely because its number is inconvenient; exclusion requires the
   rationale to fail verification.

After any exclusion, re-run disagreement detection on the surviving scores only; the
majority/BLOCK rules likewise count surviving scores. If exclusion leaves two valid scores on
a dimension: within 1 point — treat as the majority pair (mean of the two); further apart —
deadlock, ESCALATE. A single surviving score never decides a dimension: re-dispatch one fresh
replacement verifier (same role as the excluded member) for that dimension, or ESCALATE.

Adjudicate on evidence; never average disagreement away. A naive mean over one confused judge
fails good work; a rubber stamp over one ignored judge ships bad work. Both errors are the
same omission: nobody checked the evidence.

## Devil's Advocate behavioral contract

Generic skepticism is statistically indistinguishable from no intervention. The DA carries an
explicit mandate:

1. MUST oppose: find the strongest case for rejection.
2. MUST be specific: generic skepticism is worthless; name the exact failure scenario.
3. MUST name attack vectors: for each concern, describe the concrete situation where the
   deliverable fails.
4. Scores conservatively: 0.5-1.5 points lower than a neutral evaluator on dimensions with
   legitimate concerns; takes the worst reasonable interpretation of each dimension.
5. Never a perfect score unless ALL attack vectors are exhausted and nothing was found —
   which should be rare. "I genuinely cannot find a flaw" must be earned, stated, and rare.

The DA is not contrarian for sport — it is the last line of defense against shipping broken,
incomplete, or subtly flawed work.

### Attack vectors (all seven)

1. **Hidden assumptions** — what must be true for this to work that is not stated?
2. **Failure scenarios** — concrete situations where this breaks.
3. **Over-engineering** — is complexity hiding bugs?
4. **Missing constraints** — what inputs produce wrong behavior?
5. **Drift risk** — what external dependencies could break this?
6. **Spec gaps** — what do the acceptance criteria require that this does not deliver?
7. **Integration fragility** — how does this interact badly with the surrounding system?

The DA's conservative bias is intentional and already absorbed by the thresholds — every
mean is expected to contain one adversarial scorer. Never "correct" DA scores upward before
the math; adjudication handles the case where its evidence is false.

### ESCALATE conditions

The DA writes the literal signal `ESCALATE: <reason>` when ANY of:

1. The DA scores a dimension at 1 while both other members score it 7 or higher (extreme
   disagreement).
2. The DA identifies a safety or correctness concern the majority dismisses.
3. After synthesis, the DA describes a specific failure scenario the others cannot rebut.

The two 1-score rules are distinct: any lone DA 1 fires a synthesis round (disagreement
trigger 3); a DA 1 against two peers at 7+ additionally meets escalation condition 1.

Limits: escalation surfaces the concern to the human; the DA cannot block consensus alone, but
it can never be silently overruled on correctness. If the orchestrator judges (on evidence)
that the other two genuinely cannot rebut a correctness scenario, stop the run and surface it
even without the literal signal. The same protection extends to every role: a verified
correctness or safety scenario from Verifier-A or Verifier-B that survives rebuttal is treated
exactly like an unrebutted Devil's Advocate scenario — surface it; never average past it.

### Human escalation package (what the orchestrator surfaces)

When escalating to the human, deliver: the DA's scenario verbatim, all three scorecards on the
disputed dimensions, the rebuttals attempted in synthesis, the adjudication findings (which
claims were verified or refuted), the artifact location, and the orchestrator's suggested next
step. Never escalate a bare "the panel disagrees."

## Minority-report persistence

Every dissenting position that survives synthesis — and every refuted score — is logged, never
deleted:

```
Dissent: <dimension>
  Source: <panel role>
  Score: <their score> vs consensus <consensus score>
  Rationale: <their justification>
  Rebuttal: <their one-sentence rebuttal>
  Status: logged (majority overruled) | refuted (evidence failed verification)
```

Minority reports feed future iterations as improvement signals. Dissent is data.

## Orchestrator checklist

1. Freeze acceptance criteria and dimensions; pick weights and target.
2. Verify the doer's report against the actual diff and fresh command output.
3. Spawn 3 verifiers in parallel with the context-wall inputs only.
4. Collect 3 JSON scorecards; discard evidence-free findings.
5. Run adjudication on outliers; exclude refuted scores; log refuted dissent.
6. Run disagreement detection (4 triggers).
7. No trigger: compute final score; go to step 10.
8. Trigger fired: collect rationales (disputed dimensions, max 500 words), anonymize as
   Rationale-1/2/3, shuffle, share simultaneously, collect revise-or-rebut responses.
9. Run the convergence test: converged / majority / deadlock.
10. Map to verdict via the thresholds table; honor any `ESCALATE:` signal.
11. Write the ledger entry: round verdicts, dissents (including refuted), caveats, and the
    panel-round count (doer retries are a separate, complexity-weighted budget; whichever
    budget exhausts first escalates).
12. SHIP: proceed. ITERATE: build the feedback packet, spawn a fresh doer, count the round —
    max 3 panel rounds per slice, then ESCALATE. BLOCK or ESCALATE: stop and surface.

Malformed scorecards: a scorecard with invalid or partial JSON, missing dimensions, or no
recommendation is rejected and its verifier re-dispatched ONCE (fresh, same role; this counts
as a dispatch, not a panel round). Still malformed after the retry: treat the member as
excluded and apply the surviving-member rules from adjudication.

Cost shape: a fully contested slice costs up to 9 scorecard dispatches (3 verifiers x up to
3 rounds) plus up to 9 revise-or-rebut responses (rationales are harvested from the scorecards, not
separately dispatched) — roughly 18 panel dispatches — before doer
retries and any plan-vetting calls. If a slice's value cannot justify that
worst case, it is below the tribunal threshold — verify it with a single command or one
reviewer instead. Verification spend is still small next to the cost of a shipped subtle
defect; the threshold exists to stop mid-size over-application, not to excuse skipping gates.

## Research basis

| Claim | Finding | Implication |
|---|---|---|
| Explicit opposition mandate outperforms generic skepticism | Role-assignment studies: an explicit opposition mandate roughly doubles disagreement detection; "think critically" is statistically indistinguishable from no intervention | The DA gets a MUST-oppose contract; soft skepticism prompts are banned |
| Per-criterion atomic evaluation prevents halo effects | Rubric-decomposition studies on per-criterion atomic evaluation | Each dimension scored in its own pass; holistic scores forbidden |
| Evidence anchoring prevents hallucinated justifications | Rubric-anchoring / calibration work | Verbatim quotes for high scores; file:line for low scores; evidence-free findings discarded |
| Named adversarial hypotheses beat blind skepticism | Oracle-poisoning studies: blind skepticism's catch rate equals its false-positive rate | The DA must enumerate concrete attack vectors |
| Calibrated confidence improves belief propagation | Confidence-modulated debate studies | Per-dimension 0.0-1.0 confidence; feeds trigger 4 |
| One synthesis round is the cost-quality sweet spot | Multi-round deliberation studies: full deliberation costs tens of times more tokens; extra rounds converge to the most verbose agent | Hard cap of one synthesis round |
| Anonymization reduces source bias | Council/judging patterns | Strip role labels, shuffle order before sharing rationales |
| Minority reports preserve institutional knowledge | Decision-packet patterns in delegation frameworks | All surviving dissent persisted and fed forward |
| 3-member panels near-optimal for focused evaluation | Delegation studies: ~4 optimal for complex tasks, diminishing returns above | 3 roles as the cost-quality choice |
| Escalation power prevents majority tyranny on safety | Adversarial-review practice | `ESCALATE:` signal; DA never silently overruled on correctness |
