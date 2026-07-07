# Rubric Templates

Rubric YAML format and templates for scoring skills against gold standards.

## Contents

- Rubric YAML Schema
- Universal Dimensions
- Domain-Specific Templates
- Custom Dimension Guidelines
- Judge Variance Considerations

---

## Rubric YAML Schema

```yaml
name: <skill-name>-rubric
dimensions:
  - name: <string>          # lowercase, underscores (e.g., content_accuracy)
    weight: <0.0-1.0>       # must sum to 1.0 across all dimensions
    scale: "1-10"            # scoring scale
    criteria: <string>       # what this dimension measures
  # 5-10 dimensions total
target_score: <0.0-1.0>     # normalized target (0.85 = 85%)
max_iterations: <int>        # experiment budget (default: 20)
plateau_window: <int>        # consecutive failures before plateau alert (default: 5)
```

Weights MUST sum to 1.0. The `overall_score` METRIC is computed as:

```
overall_score = sum(dimension_score / scale_max * weight) for each dimension
```

This produces a normalized 0.0-1.0 value. Example: score 8 on a 1-10 scale with weight 0.20 = 8/10 * 0.20 = 0.16.

---

## Universal Dimensions

These dimensions apply to every skill. Include all five, adjusting weights per domain.

| Dimension | Default Weight | Scale | Criteria |
|-----------|---------------|-------|----------|
| correctness | 0.20 | 1-10 | Instructions are technically accurate and executable. No hallucinated APIs, wrong syntax, or incorrect behavior descriptions. |
| completeness | 0.15 | 1-10 | All necessary sections present. Edge cases addressed. No critical gaps in the workflow. |
| clarity | 0.15 | 1-10 | A naive agent can follow the instructions without ambiguity. Terminology is consistent. Examples are concrete. |
| consistency | 0.10 | 1-10 | Aligns with existing codebase conventions, naming patterns, and integration points. |
| predictability | 0.10 | 1-10 | Drives the same process every run: completion criteria are checkable and exhaustive; no vague gates; no no-op instructions the model already obeys (see skill-craft-principles.md). |

Remaining weight (0.30) is allocated to domain-specific dimensions.

---

## Domain-Specific Templates

### Code-Behavior Skill (e.g., production-grade, deslop)

```yaml
name: code-behavior-rubric
dimensions:
  - name: correctness
    weight: 0.20
    scale: "1-10"
    criteria: "Rules are technically accurate. Code examples compile and run correctly."
  - name: completeness
    weight: 0.10
    scale: "1-10"
    criteria: "All relevant coding scenarios covered. Edge cases addressed."
  - name: clarity
    weight: 0.15
    scale: "1-10"
    criteria: "Rules are unambiguous. A naive agent follows them without misinterpretation."
  - name: consistency
    weight: 0.10
    scale: "1-10"
    criteria: "Matches existing project conventions and coding standards."
  - name: predictability
    weight: 0.10
    scale: "1-10"
    criteria: "Same process every run: completion criteria checkable and exhaustive; no vague gates or no-op rules."
  - name: coverage
    weight: 0.10
    scale: "1-10"
    criteria: "Every pattern from the gold standard source material appears in the skill."
  - name: actionability
    weight: 0.15
    scale: "1-10"
    criteria: "Rules are concrete and executable, not abstract platitudes."
  - name: portability
    weight: 0.10
    scale: "1-10"
    criteria: "Works across projects without hardcoded names or project-specific assumptions."
target_score: 0.85
max_iterations: 20
plateau_window: 5
```

### Domain-Workflow Skill (e.g., product documentation, report aggregation)

```yaml
name: domain-workflow-rubric
dimensions:
  - name: correctness
    weight: 0.15
    scale: "1-10"
    criteria: "Workflow steps are accurate. Output matches domain requirements."
  - name: completeness
    weight: 0.15
    scale: "1-10"
    criteria: "All workflow steps present. Handles normal and exceptional paths."
  - name: clarity
    weight: 0.10
    scale: "1-10"
    criteria: "Instructions are unambiguous. Templates are concrete."
  - name: consistency
    weight: 0.10
    scale: "1-10"
    criteria: "Follows existing team/org conventions and terminology."
  - name: predictability
    weight: 0.10
    scale: "1-10"
    criteria: "Same process every run: completion criteria checkable and exhaustive; no vague gates or no-op rules."
  - name: content_accuracy
    weight: 0.15
    scale: "1-10"
    criteria: "Facts, names, dates preserved without hallucination or distortion."
  - name: curation
    weight: 0.10
    scale: "1-10"
    criteria: "Appropriate editorial judgment on what to include/exclude/summarize."
  - name: output_match
    weight: 0.15
    scale: "1-10"
    criteria: "Output serves equally well as the human-written gold standard reference."
target_score: 0.85
max_iterations: 25
plateau_window: 5
```

### Pipeline Skill (e.g., data generator, content pipeline)

```yaml
name: pipeline-rubric
dimensions:
  - name: correctness
    weight: 0.20
    scale: "1-10"
    criteria: "Pipeline produces valid output. No errors, crashes, or malformed artifacts."
  - name: completeness
    weight: 0.10
    scale: "1-10"
    criteria: "All pipeline stages documented. Error handling present."
  - name: clarity
    weight: 0.10
    scale: "1-10"
    criteria: "Pipeline flow is clear. Input/output contracts are explicit."
  - name: consistency
    weight: 0.10
    scale: "1-10"
    criteria: "Matches existing infrastructure patterns and conventions."
  - name: predictability
    weight: 0.10
    scale: "1-10"
    criteria: "Same process every run: completion criteria checkable and exhaustive; no vague gates or no-op rules."
  - name: output_quality
    weight: 0.15
    scale: "1-10"
    criteria: "Pipeline output matches or exceeds gold standard quality."
  - name: reliability
    weight: 0.15
    scale: "1-10"
    criteria: "Pipeline handles failures gracefully. Idempotent where possible."
  - name: efficiency
    weight: 0.10
    scale: "1-10"
    criteria: "Reasonable resource usage. No unnecessary API calls or processing."
target_score: 0.85
max_iterations: 20
plateau_window: 5
```

---

## Custom Dimension Guidelines

When adding domain-specific dimensions:

1. **Name**: lowercase, underscores, descriptive (e.g., `voice_alignment`, `api_coverage`)
2. **Weight**: allocate from the 0.30 pool. Higher weight = more important to the domain.
3. **Criteria**: Write as a falsifiable statement. "The skill does X" is testable. "The skill is good" is not.
4. **Scale**: Use 1-10 for all dimensions (consistent with universal dimensions).

### Good Criteria Examples

- "Every gold standard pattern appears in the skill output" (testable)
- "Every step's completion criterion is checkable -- the agent can tell done from not-done" (testable)
- "Instructions produce output within 10% of reference length" (measurable)
- "No org-specific names or hardcoded references appear in the body" (scannable)
- "All file references resolve to existing files" (verifiable)

### Bad Criteria Examples

- "The skill is well-written" (subjective, not actionable)
- "Output is high quality" (vague, not measurable)
- "Follows best practices" (which practices? unmeasurable)

---

## Judge Variance Considerations

From case study experience (extensive experiments):

- Judge variance is typically 0.2-0.3 points on a 1-10 scale
- Real improvements need to exceed judge variance to be meaningful
- Different judge models have different variance profiles:
  - Gemini Flash: low variance (~0.1), tends lenient
  - Gemini Pro: higher variance (~0.57)
  - Claude: strictest baseline scores
- Using multiple judges improves confidence but increases cost

When plateau detection triggers, check if the plateau is within judge variance -- if so, the skill may already be at the measurement ceiling.
