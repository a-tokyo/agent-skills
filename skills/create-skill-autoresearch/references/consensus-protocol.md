# Consensus Protocol Reference

Structured multi-agent verification protocol for the factory's Phase 5. Three independent verifiers score the skill artifact against the rubric, then resolve disagreements through anonymized synthesis.

---

## Protocol Overview

```
Phase 1: Independent Scoring
  3 panel members score in isolation. No cross-talk.
  Each dimension scored atomically (separate evaluation pass).

Phase 2: Agreement Check
  All dimensions within 1 point spread? → Consensus reached.
  Any dimension spread >= 2? → Trigger synthesis round.
  DA scores any dimension at 1? → Trigger synthesis round.

Phase 3: Synthesis Round (if triggered)
  Each member writes rationale on disputed dimensions.
  Rationales anonymized and shared.
  Members may revise scores with justification.

Phase 4: Resolution
  Converged (all within 1 point) → weighted average.
  Majority (2-of-3 agree) → majority score, dissent logged.
  Deadlock (all 3 divergent) → escalate to user.
```

---

## Panel Composition

| Role | Archetype | Focus | Bias Direction |
|------|-----------|-------|----------------|
| **Verifier-A** | Quality Assessor | Correctness, completeness, clarity, spec adherence | Neutral |
| **Verifier-B** | Utility Assessor | Real-world usability, edge cases, integration, developer experience | Neutral |
| **Devil's Advocate** | Adversarial Challenger | Failure modes, hidden assumptions, missing constraints, drift risk | Explicitly negative |

### Context Wall

Panel members receive ONLY:
- The skill SKILL.md and reference files
- The gold standards (input/output pairs or references)
- The rubric (dimensions, weights, criteria)
- Premortem risks (from Phase 5.1)

Panel members do NOT receive:
- Research notes or dossier
- Experiment logs or ASI
- Builder context or conversation history
- Previous scores from other panel members

---

## Scoring Rules

### Per-Criterion Atomic Evaluation

Score each rubric dimension in an independent evaluation pass. This prevents halo effects where a strong dimension inflates a weak one.

### Evidence Requirement

- Score of 8+ (on 1-10 scale): must cite at least 1 verbatim quote from the artifact as evidence
- Score of 3- (on 1-10 scale): must cite the specific section/line where the failure occurs
- Mid-range scores (4-7): evidence recommended but not required

### Confidence Signals

Each score includes a confidence value (0.0-1.0):
- 0.9-1.0: Very confident, clear evidence
- 0.7-0.8: Confident, minor ambiguity
- 0.5-0.6: Uncertain, could go either way
- Below 0.5: Low confidence, limited evidence

Confidence is used in aggregation: low-confidence scores are weighted down during averaging.

---

## Disagreement Detection

Synthesis round is triggered when ANY of these conditions are met:

1. **Score spread >= 2 points** on any single dimension across panel members
2. **Pass/fail split**: One member's weighted overall >= target_score while another's is < (target_score - 0.15)
3. **DA veto**: Devil's Advocate scores any dimension at 1 (automatic trigger)
4. **Confidence divergence**: Any member marks confidence < 0.5 on a dimension where others mark > 0.8

---

## Synthesis Round

### Process

1. Each panel member writes a **rationale** (max 500 words) explaining their scores on disputed dimensions only
2. Rationales are **anonymized** (stripped of role labels) and shared with all panel members simultaneously
3. Each member may **revise scores** on disputed dimensions only, with written justification for any change
4. If a member does NOT change their score, they must provide a **one-sentence rebuttal** to the strongest opposing argument

### Convergence Test

After synthesis:
- **Converged**: All dimensions within 1 point spread → take weighted average of all 3 scores
- **Majority**: 2-of-3 agree (within 1 point) → majority score adopted, dissenting concern logged in craft-decisions
- **Deadlock**: All 3 scores remain > 1 point apart on any dimension → escalate to user

---

## Final Score Computation

```
dimension_consensus = mean(agreeing_scores)  # 2 or 3 scores depending on convergence
overall_score = sum(dimension_consensus / scale_max * weight) for each dimension
```

### Pass/Fail Thresholds

| Condition | Action |
|-----------|--------|
| overall_score >= target_score AND no dimension blocked | **SHIP** -- skill is production ready |
| overall_score >= target_score - 0.10 | **SHIP WITH CAVEATS** -- log concerns, proceed |
| overall_score < target_score - 0.10 | **ITERATE** -- feed panel feedback back to autoresearch |
| Any dimension score < 3 (on 1-10) by majority | **BLOCK** -- address blocking concern before shipping |

### ITERATE Feedback

When the panel returns ITERATE, extract and structure the feedback:
1. Top concerns from each panel member
2. Failure scenarios from the Devil's Advocate
3. Per-dimension scores with justifications
4. Add specific improvement hypotheses to `autoresearch.ideas.md`

---

## Devil's Advocate Rules

The DA role is critical. Research shows generic skepticism ("be critical") is statistically indistinguishable from no intervention. The DA must have explicit behavioral mandates.

### Behavioral Contract

1. **Must oppose**: Find the strongest case for rejection
2. **Must be specific**: Generic skepticism is worthless. Name the exact failure scenario.
3. **Must name attack vectors**: For each concern, describe the concrete situation where the skill fails
4. **Score conservatively**: Scores should be 0.5-1.5 points lower than a neutral evaluator on dimensions with legitimate concerns
5. **Never score 5/5 (or 10/10)**: Unless ALL attack vectors exhausted and nothing found (should be rare)

### Attack Vectors

1. **Hidden assumptions**: What must be true for this to work that isn't stated?
2. **Failure scenarios**: Concrete situations where this breaks
3. **Over-engineering**: Is complexity hiding bugs?
4. **Missing constraints**: What inputs produce wrong behavior?
5. **Drift risk**: What external dependencies could break this?
6. **Spec gaps**: What does the rubric require that this doesn't deliver?
7. **Integration fragility**: How does this interact with other system components?

### Escalation Power

The DA may write `ESCALATE: <reason>` when:
- DA scores any dimension at 1 AND the other two score it at 7+ (extreme disagreement)
- DA identifies a safety/correctness concern the majority dismisses
- DA describes a specific failure scenario the others cannot rebut

Escalation surfaces the concern to the user for human judgment. The DA cannot block consensus alone, but ensures critical risks are not silently overruled.

---

## Orchestrator Logic

### Spawning the Panel

```
For each panel member (Verifier-A, Verifier-B, Devil's Advocate):
  1. Spawn a generalPurpose subagent
  2. Provide: skill content + gold standards + rubric + premortem risks
  3. Provide: role-specific system prompt (from pipeline-phases.md)
  4. Request: JSON output with per-dimension scores, confidence, evidence
  5. Run all 3 in parallel (no cross-talk)
```

### Processing Results

```
1. Collect all 3 JSON scoring outputs
2. Run disagreement detection
3. If no disagreement → compute final score → done
4. If disagreement → run synthesis round:
   a. Extract disputed dimensions
   b. Anonymize rationales
   c. Share with all members
   d. Collect revised scores
   e. Run convergence test
5. Compute final score based on convergence outcome
6. If SHIP → output final skill
7. If ITERATE → structure feedback → return to autoresearch
8. If BLOCK → highlight blocking dimension → user review
9. If DA escalation → surface to user
```

### Minority Reports

Every dissenting score that survives synthesis (i.e., the member maintained their position) is logged as a minority report in `work/experiments/craft-decisions.md`:

```
DNN -- Panel dissent: <dimension>
  Source: <panel role>
  Score: <their score> vs consensus <consensus score>
  Rationale: <their justification>
  Rebuttal: <their rebuttal to opposing argument>
  Status: logged (majority overruled)
```

Minority reports are available to future autoresearch iterations as potential improvement directions.

---

## Anti-Patterns

1. **Generic skepticism**: "Be critical" produces noise. Always assign explicit attack vectors.
2. **Consensus pressure**: Never show members each other's scores before independent evaluation.
3. **Score anchoring**: Never provide "expected" scores or prior iteration scores to the panel.
4. **Halo inflation**: Never ask for a single holistic score. Always decompose into dimensions.
5. **Unbounded debate**: Cap synthesis at 1 round. More rounds converge to the most verbose agent, not the most correct.
6. **DA drift to agreement**: The DA prompt must prohibit agreement language.
7. **Ignoring dissent**: Every minority report must be persisted and available to subsequent iterations.

---

## Research Basis

Key findings that shaped this protocol:

| Finding | Source | Impact on Design |
|---------|--------|-----------------|
| Explicit "you must oppose" achieves 99.2% disagreement detection vs 48.3% baseline | OpenReview devil's advocate study | DA has explicit behavioral mandate |
| Per-criterion atomic evaluation prevents halo effects | Autorubric (arXiv:2603.00077) | Each dimension scored independently |
| Evidence-anchoring prevents hallucinated justifications | Rulers framework | Verbatim quotes required for extreme scores |
| Named/specific adversarial hypotheses >> generic skepticism | Oracle Poisoning (arXiv:2605.09822) | DA must name specific failure scenarios |
| Calibrated confidence signals improve belief propagation | Confidence-Modulated Debate (arXiv:2601.19921) | Confidence 0.0-1.0 per score |
| Single synthesis round balances quality vs cost | DCI efficiency findings | Cap at 1 round of rationale exchange |
