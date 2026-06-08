# Pipeline Phases Reference

Detailed instructions for each phase of the create-skill-autoresearch factory.

## Contents

- Phase 1: Interview -- Detailed Question Flow
- Phase 2: Research -- Subagent Orchestration
- Phase 3: Draft -- Design-First Approach (DESIGN.md template, `evaluate.sh` contract, judge templates)
- Phase 4: Autoresearch -- Integration Details (data split, overfitting detection)
- Phase 5: Verify -- Panel Prompt Templates (Verifier-A/B, Devil's Advocate, synthesis)

---

## Phase 1: Interview -- Detailed Question Flow

### Question Sequence

Ask the user these questions directly (use whatever questioning mechanism your agent supports). Gather all answers before proceeding.

**Q1 -- Purpose**: "What skill do you want to build? What problem does it solve?"
- Follow up: "Who is the target user? An AI coding agent? A CLI tool? A human?"
- Follow up: "What does success look like when this skill is used correctly?"

**Q2 -- Gold Standards**: "Do you have examples of what good output looks like?"
- If YES: "Where are they? What format? How many?"
- If NO: "Can we find reference materials? Previously solved problems? Expert outputs?"
- If INSUFFICIENT (< 3): "Can we create synthetic examples together? Or find more reference materials?"

**Q3 -- Study Materials**: "What should I study to understand this domain?"
- Probe for: docs, code, transcripts, specs, style guides, existing skills, external references

**Q4 -- Constraints**: "Any constraints on the skill? Conventions? Integration points? Anti-patterns?"

**Q5 -- Confirm**: Present the summary table. Wait for explicit confirmation.

### Gold Standard Intake Formats

The factory supports these gold standard formats:

| Format | Example | How to Use |
|--------|---------|------------|
| Input/output pairs | `{ "input": "...", "expected_output": "..." }` | Direct comparison scoring |
| Reference artifacts | Completed documents, code files | Holistic quality comparison |
| Previously solved tasks | Task descriptions + solutions | Run skill on task, compare to solution |
| Quality reports | Existing benchmark scores | Calibrate rubric against known scores |

The user drops gold standards and study materials into `input/` in any structure they prefer. The factory scans `input/`, classifies each item (gold standard vs. study material), and writes its own derived index to `work/manifest.yaml`. The user is NOT asked to hand-author the manifest — they just confirm it looks correct.

```yaml
# work/manifest.yaml
pairs:
  - input: "input-01.md"
    reference: "output-01.md"
    tags: ["training"]
  - input: "input-02.md"
    reference: "output-02.md"
    tags: ["validation"]
  - input: "input-03.md"
    reference: "output-03.md"
    tags: ["test"]
```

---

## Phase 2: Research -- Subagent Orchestration

### Spawning Researchers

For each cluster of study materials, spawn an `explore` subagent with:

```
Prompt: "Study the following materials thoroughly and produce a research note.

Materials to study:
<list of files/paths>

Your research note must cover:
1. Key patterns and conventions found in the materials
2. Quality signals -- what distinguishes good from bad output
3. Domain-specific terminology and concepts
4. Potential rubric dimensions (measurable quality aspects)
5. Anti-patterns or common mistakes to avoid

Write your findings to work/research/<NN>-<topic>.md

Be thorough. Read ALL relevant files. Quote specific examples."
```

### Synthesis Template

After all researchers complete, create `work/research/00-synthesis.md`:

```markdown
# Research Synthesis

## Cross-Cutting Patterns
<Patterns that appear across multiple study materials>

## Quality Signals
<What makes good output good? What makes bad output bad?>

## Rubric Dimensions (Proposed)
<List of measurable dimensions with descriptions>

## Conventions to Follow
<Style, voice, structure, terminology conventions>

## Anti-Patterns to Avoid
<Common mistakes, bad patterns, traps>

## Key References
<Links to the most important study materials for the builder>
```

---

## Phase 3: Draft -- Design-First Approach

### DESIGN.md Template

Before writing any SKILL.md code, create `work/experiments/DESIGN.md`:

```markdown
# <Skill Name> -- Design Document

## Purpose
<1-2 sentence description of what this skill does>

## Structure
- Body sections: <list of major sections>
- Reference files: <list of planned reference files, if any>
- Estimated body length: <N lines>

## Conventions
- Frontmatter: name=<name>, description follows WHAT+WHEN pattern
- Voice: <imperative third-person / other>
- Terminology: <key terms and their definitions>

## Integration Points
- <skills this integrates with and how>

## Design Decisions
- D1: <decision and rationale>
- D2: <decision and rationale>
```

### evaluate.sh Contract

The evaluation script is the bridge between the skill and the autoresearch loop.
Skills are markdown instructions for AI agents -- not executables. To evaluate a skill,
the script must invoke an LLM with the SKILL.md as a system prompt and the test case
input as the user message, then compare the LLM's output to the gold standard reference.

```bash
#!/bin/bash
# evaluate.sh -- Run skill on a test case and score via LLM-as-judge
#
# Usage: ./evaluate.sh <test-case-path>
# Output: METRIC lines to stdout
#
# Contract:
# 1. Takes a gold standard test case path as argument
# 2. Extracts input from the test case
# 3. Calls an LLM with SKILL.md as system prompt + test input as user message
# 4. Captures the LLM output
# 5. Runs deterministic checks (line count, frontmatter, links) → emit METRIC lines
# 6. Sends output + reference to an LLM judge for subjective dimensions → emit METRIC lines
# 7. Computes and emits METRIC overall_score=<weighted_average>
#
# Requirements: JUDGE_API_BASE, JUDGE_API_KEY, JUDGE_MODEL env vars for LLM steps
# Fallback: deterministic-only scoring if no LLM API configured

set -euo pipefail

TEST_CASE="$1"
RUBRIC="work/evaluation/rubric.yaml"
SKILL="output/<skill-name>/SKILL.md"

# Step 1-2: Extract input
INPUT=$(cat "$TEST_CASE")
REFERENCE=$(cat "$(dirname "$TEST_CASE")/$(basename "$TEST_CASE" .md | sed 's/input/output/').md")

# Step 3-4: Invoke skill via LLM API (curl to OpenAI-compatible endpoint).
# Build the JSON body with a real encoder -- never string-interpolate file contents into
# JSON (quotes/newlines in the skill or the input would otherwise produce invalid JSON).
REQUEST=$(SKILL_PATH="$SKILL" USER_INPUT="$INPUT" MODEL="$JUDGE_MODEL" python3 -c '
import json, os
print(json.dumps({
    "model": os.environ["MODEL"],
    "messages": [
        {"role": "system", "content": open(os.environ["SKILL_PATH"]).read()},
        {"role": "user", "content": os.environ["USER_INPUT"]},
    ],
}))')
SKILL_OUTPUT=$(curl -s "${JUDGE_API_BASE}/chat/completions" \
  -H "Authorization: Bearer $JUDGE_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$REQUEST" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])")

# Step 5: Deterministic checks → METRIC lines
# Step 6: LLM-as-judge on subjective dimensions → METRIC lines
# Step 7: Weighted average → METRIC overall_score=<value>
```

See `self-test/evaluation/evaluate.sh` in the [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness) repo for a complete reference implementation.

The specific approach depends on the skill type:
- **Instruction skills**: Call LLM with SKILL.md as system prompt (most common)
- **Pipeline skills**: Run the pipeline script, compare output artifacts to reference
- **Code-behavior skills**: Run the skill on a coding task, compare output to reference solution

### Multi-Judge Configuration

When using multiple LLM judges for higher confidence scoring:

```yaml
# work/evaluation/judges.yaml
judges:
  - model: "<model-1>"       # e.g., a strong reasoning model
    weight: 1.0
  - model: "<model-2>"       # e.g., a different model family
    weight: 1.0
  - model: "<model-3>"       # optional third judge
    weight: 0.5              # lower weight if less trusted
aggregation: "mean"           # "mean" or "median"
```

The `evaluate.sh` script should:
1. Run each judge independently on the same skill output + reference pair
2. Collect per-dimension scores from each judge
3. Aggregate using the configured method (mean or median)
4. Emit the aggregated scores as METRIC lines
5. Optionally emit per-judge variance: `METRIC judge_variance_<dim>=<value>`

High per-judge variance on a dimension signals ambiguous criteria -- consider rewriting that rubric dimension to be more precise.

### evaluate-checks.sh Example

```bash
#!/bin/bash
# Correctness gate -- must pass before metric measurement
set -euo pipefail
SKILL="$1"
LINES=$(wc -l < "$SKILL")
[ "$LINES" -lt 500 ] || { echo "FAIL: $LINES lines (limit 500)"; exit 1; }
head -1 "$SKILL" | grep -q "^---" || { echo "FAIL: missing frontmatter"; exit 1; }
grep -q "^name:" "$SKILL" || { echo "FAIL: missing name field"; exit 1; }
echo "All checks passed"
```

### LLM-as-Judge Prompt Template

```markdown
# Evaluation Judge

You are scoring a skill's output against a gold standard reference.
Score EACH dimension independently. Do not let one dimension influence another.

## Rubric
{{rubric_dimensions_with_criteria}}

## Gold Standard Reference
{{reference_output}}

## Skill Output (to evaluate)
{{skill_output}}

## Scoring Instructions
For each dimension:
1. Read the criteria carefully
2. Compare the skill output to the reference
3. Assign a score on the specified scale
4. Provide a brief justification with a verbatim quote

## Output Format (JSON)
{
  "scores": {
    "<dimension_name>": {
      "score": <number>,
      "justification": "<brief reason with evidence>"
    }
  }
}
```

---

## Phase 4: Autoresearch -- Integration Details

### Preparing the Autoresearch Invocation

The factory prepares these inputs for the autoresearch skill:

1. **Goal statement**: "Improve <skill-name> quality as measured by LLM-as-judge scoring against gold standards"
2. **Metric command**: `./work/evaluation/evaluate.sh` (must emit METRIC lines)
3. **Primary metric**: `overall_score` (weighted average of all rubric dimensions)
4. **In-scope files**: Only the skill being built (`output/<skill-name>/SKILL.md` and `output/<skill-name>/references/*`)
5. **Out-of-scope**: Everything else (`input/` and `work/` — evaluation scripts, gold standards, research)
6. **Budget**: From `rubric.yaml` `max_iterations` field
7. **Checks**: `./work/evaluation/evaluate-checks.sh` if it exists

### Seeding the Ideas Backlog

Before starting autoresearch, populate `autoresearch.ideas.md` with:

```markdown
# Experiment Ideas

## From Research Synthesis
- [ ] <idea from research that isn't in the draft yet>
- [ ] <pattern from gold standards not reflected in skill>

## Per-Dimension Improvement
- [ ] <specific idea to improve the weakest dimension>
- [ ] <specific idea to improve the second weakest>

## From Gold Standard Analysis
- [ ] <pattern in reference outputs the skill doesn't produce>
```

### Data Split Management

```yaml
# work/evaluation/data-split.yaml
strategy: "70-20-10"  # or "leave-one-out"
training:
  - "input/input-01.md"
  - "input/input-02.md"
  # ...
validation:
  - "input/input-08.md"
  - "input/input-09.md"
test:
  - "input/input-10.md"
```

**Overfitting detection** (70-20-10 split only):

Run validation checks **adaptively** -- not on a fixed schedule. Trigger a validation check when:
1. **An experiment is kept** -- every improvement changes the overfitting risk profile
2. **A plateau is detected** -- to distinguish a real ceiling from training-set-specific saturation
3. **A large jump occurs** -- training score improves by > 0.05 in a single experiment (sudden gains are suspicious)

Compare training and validation score trends:
- Training up, validation up → healthy improvement, continue
- Training up, validation flat → early warning, monitor closely
- Training up, validation down (> 0.05 gap) → overfitting detected, flag and consider reverting to last validation-stable commit

Log validation checks in `autoresearch.jsonl` as:
```json
{ "type": "validation_check", "training_score": 0.82, "validation_score": 0.78, "trigger": "kept", "experiment": 10, "timestamp": "..." }
```

For leave-one-out (< 10 gold standards):
```yaml
strategy: "leave-one-out"
all_cases:
  - "input/input-01.md"
  - "input/input-02.md"
  - "input/input-03.md"
rotation: "each experiment holds out a different case"
```

**Overfitting detection for leave-one-out**: Since there is no fixed validation set, use per-case score variance as the signal:
- After each kept experiment, record per-case scores (not just the aggregate)
- If the variance across cases increases while the mean improves, the skill is specializing for some cases at the expense of others
- Flag when any single case drops by > 1.0 point while others improve
- Log per-case scores in `autoresearch.jsonl`: `"per_case_scores": {"input-01": 8.2, "input-02": 7.1, ...}`

---

## Phase 5: Verify -- Panel Prompt Templates

### Verifier-A: Quality Assessor

```markdown
# Role: Quality Assessor

You are an independent quality verifier evaluating an AI agent skill.
Assess STRUCTURAL QUALITY: correctness, completeness, clarity, spec adherence.

## Principles
- Evaluate the artifact AS-IS, not its potential
- Ground every claim in evidence from the artifact
- Score each dimension INDEPENDENTLY
- Express calibrated confidence (0.0-1.0) per score
- You have NOT seen any other evaluator's scores or the building process

## Input
- Skill: {{skill_content}}
- Gold standards: {{gold_standards}}
- Rubric: {{rubric}}
- Premortem risks: {{premortem_risks}}

## Output (JSON)
{
  "scores": {
    "<dimension>": { "score": <N>, "confidence": <0-1>, "evidence": "<quote>" }
  },
  "overall_assessment": "<2-3 sentences>",
  "top_concern": "<single most important issue>",
  "pass_recommendation": "SHIP|SHIP_WITH_CAVEATS|ITERATE|BLOCK"
}
```

### Verifier-B: Utility Assessor

```markdown
# Role: Utility Assessor

You are an independent utility verifier. Assess PRACTICAL VALUE:
real-world usability, edge cases, integration quality, developer experience.

## Guiding Question
"Would this actually work in production for the intended agent?"

## Additional Checks
- Does the skill handle the unhappy path?
- Are there implicit assumptions that break in different contexts?
- Is it over-engineered for its stated purpose?
- Would an agent struggle to follow these instructions?

## Input
- Skill: {{skill_content}}
- Gold standards: {{gold_standards}}
- Rubric: {{rubric}}
- Premortem risks: {{premortem_risks}}

## Output (JSON)
{
  "scores": {
    "<dimension>": { "score": <N>, "confidence": <0-1>, "evidence": "<quote>" }
  },
  "overall_assessment": "<2-3 sentences>",
  "top_concern": "<single most important issue>",
  "edge_cases_found": ["<list>"],
  "pass_recommendation": "SHIP|SHIP_WITH_CAVEATS|ITERATE|BLOCK"
}
```

### Devil's Advocate: Adversarial Challenger

```markdown
# Role: Devil's Advocate

Your EXPLICIT MANDATE is to find reasons this skill should NOT ship.
You are the last line of defense against shipping flawed work.

## Behavioral Contract
- You MUST oppose. Find the strongest case for rejection.
- Generic skepticism is WORTHLESS. Identify SPECIFIC failure modes.
- For each concern, describe the EXACT SCENARIO in which this fails.
- If you genuinely cannot find a flaw, say so -- but this should be RARE.

## Attack Vectors
1. Hidden assumptions: What must be true that isn't stated?
2. Failure scenarios: Concrete situations where this breaks
3. Over-engineering: Complexity hiding bugs?
4. Missing constraints: Inputs that produce wrong behavior?
5. Drift risk: Dependencies that could break it?
6. Spec gaps: What does the rubric require that this doesn't deliver?

## Escalation Power
Write "ESCALATE: <reason>" if you identify a concern that:
- Could cause incorrect behavior in production
- Represents a safety/correctness issue the majority dismisses
- You can describe a specific failure scenario others cannot rebut

## Input
- Skill: {{skill_content}}
- Gold standards: {{gold_standards}}
- Rubric: {{rubric}}
- Premortem risks: {{premortem_risks}}

## Output (JSON)
{
  "scores": {
    "<dimension>": { "score": <N>, "confidence": <0-1>, "evidence": "<quote>", "attack": "<failure scenario>" }
  },
  "overall_assessment": "<adversarial summary>",
  "strongest_objection": "<single strongest reason NOT to ship>",
  "failure_scenarios": [
    { "scenario": "<concrete description>", "severity": "critical|high|medium|low" }
  ],
  "escalation": null | "ESCALATE: <reason>",
  "pass_recommendation": "SHIP|SHIP_WITH_CAVEATS|ITERATE|BLOCK"
}
```

### Synthesis Round Prompt

```markdown
# Synthesis Round: Disagreement Resolution

The panel scored this artifact independently. Disagreement exists on:
{{disputed_dimensions}}

## Your Scores vs Anonymous Peer Scores
{{score_comparison_table}}

## Anonymous Peer Rationales
{{anonymized_rationales}}

## Task
For each disputed dimension, either:
a) REVISE your score (with justification), OR
b) MAINTAIN your score (with one-sentence rebuttal to the strongest opposing argument)

You may ONLY change scores on disputed dimensions.

## Output (JSON)
{
  "revised_scores": {
    "<dimension>": {
      "original": <N>, "revised": <N>,
      "action": "REVISED|MAINTAINED",
      "justification": "<why>"
    }
  },
  "final_recommendation": "SHIP|SHIP_WITH_CAVEATS|ITERATE|BLOCK"
}
```
