# Pipeline Phases Reference

The main session's detail for the phases it runs itself: the Phase 1 interview, the Phase 2 research, and the
Phase 5.2 panel prompts. Phases 3, 4 and 5.0-5.1 run in workers, from [phase-3.md](phase-3.md),
[phase-4.md](phase-4.md) and [phase-5.md](phase-5.md).

## Contents

- Phase 1: Interview -- Detailed Question Flow (gold standard intake, manifest)
- Phase 2: Research -- Subagent Orchestration (researcher prompt, synthesis template)
- Phase 5: Verify -- Panel Prompt Templates (Verifier-A/B, Devil's Advocate, synthesis, role receipts)

---

## Phase 1: Interview -- Detailed Question Flow

### What to discover (1.1-1.5)

| Topic | Ask about | Record |
|-------|-----------|--------|
| **Purpose and domain** | the problem it solves, the domain, which agent will use it, what "success" looks like | `SKILL_PURPOSE`, `DOMAIN`, `TARGET_USER`, `SUCCESS_CRITERIA` |
| **Gold standards** | examples of "what good looks like" — input/output pairs, reference artifacts, previously solved problems, existing quality reports; where they are, what format, how many | `GOLD_STANDARD_SOURCE`, `GOLD_STANDARD_FORMAT`, `GOLD_STANDARD_COUNT` |
| **Study materials** | docs, existing code, transcripts, design docs, reference implementations, specs, style guides | `STUDY_MATERIALS` |
| **Scope and constraints** | conventions to follow, skills to integrate with, anti-patterns to avoid, target line count (default < 500), and invocation mode — model-invoked (pays permanent context load) or user-invoked (`disable-model-invocation: true`) | `CONSTRAINTS`, `INTEGRATION_SKILLS`, `ANTI_PATTERNS`, `INVOCATION_MODE` |
| **Existing skill** | is there one for this domain already? If so it is both a study material and a baseline — research it, measure it against the rubric, improve it rather than starting over | `EXISTING_SKILL`, and set mode to **upgrade** rather than **greenfield** |

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
- Follow up: "Should the agent fire this skill on its own when it detects the need (model-invoked -- its description then occupies context every turn), or only when you type its name (user-invoked, `disable-model-invocation: true` -- zero context cost, but you must remember it exists)?" Record as `INVOCATION_MODE`; it drives the description style in Phase 3.3 (writing-for-agents; fallback: standalone-fallback.md).

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

### Workspace Layout (1.6)

Each build lives in one self-contained folder, `builds/<skill-name>/`, with three zones:

```
builds/<skill-name>/
  input/                  # HUMAN: gold standards + study materials (any structure)
  work/                   # FACTORY: process artifacts (not shipped)
    manifest.yaml         #   derived gold-standard index    <- yours to correct
    research/             #   study notes and dossier
    evaluation/           #   rubric.yaml (exit criteria), evaluate.sh, judges.yaml, data-split.yaml, run-case.sh,
                          #   plugin/ (3.6 wrapper plugin + evals/), gate.json + plugin-eval.json or uplift.json (5.0)
    pids/                 #   run_owned.py job records and logs
    experiments/          #   results.tsv, autoresearch.jsonl, ideas, run.log, DESIGN.md, craft-decisions.md, premortem.md, panel/
    handoffs/             #   cross-session context (state.yaml, HANDOFF-*.md)
  output/<skill-name>/    # FACTORY: the publish-ready skill: SKILL.md, README.md, references/, scripts/ (never evaluation scripts), assets/
  BENCHMARK.md            # FACTORY: final panel scores, plugin-eval delta, verdict (Phase 5)
```

---

## Phase 2: Research -- Subagent Orchestration

### Spawning Researchers

For each cluster of study materials, spawn a read-only researcher subagent (the host's own mechanism,
[hosts.md](hosts.md); with none, run the same prompt as a fresh headless session per cluster, one after another) with:

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

## Phase 5: Verify -- Panel Prompt Templates

**Role receipts (SKILL.md 5.2).** Append this to every template below, with the paths filled in: "Write your
JSON to `<workspace>/work/experiments/panel/<role>.scores.json`, then run `python3 <skill-dir>/scripts/check_gate.py
--workspace <workspace> --panel-receipt <role> <that file>` (synthesis round: the synthesis JSON, with `--synthesis`).
A non-zero exit names what to fix in your JSON; fix it and re-run." `<role>` is `verifier-a`, `verifier-b` or
`devils-advocate`. A member that cannot run commands returns its JSON and the orchestrator runs that command on it
verbatim, unedited. The receipt checks the scores against rubric.yaml (every dimension, on its scale, evidence for
8+ and 3-) and binds them to the skill and rubric hashes; `--consensus` reads only receipts for the current hashes.

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
- For the predictability dimension: check every step ends on a checkable completion
  criterion (done vs not-done is decidable) and hunt no-op lines the model already obeys
- You have NOT seen any other evaluator's scores or the building process

## Input
- Skill: {{skill_md}} (its SKILL.md only; open a file under {{skill_dir}} by name only when a line you score points to it)
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
- Skill: {{skill_md}} (its SKILL.md only; open a file under {{skill_dir}} by name only when a line you score points to it)
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
7. Craft failure modes: premature completion, duplication, sediment, sprawl, no-op lines (writing-for-agents; fallback: standalone-fallback.md)

## Escalation Power
Write "ESCALATE: <reason>" if you identify a concern that:
- Could cause incorrect behavior in production
- Represents a safety/correctness issue the majority dismisses
- You can describe a specific failure scenario others cannot rebut

## Input
- Skill: {{skill_md}} (its SKILL.md only; open a file under {{skill_dir}} by name only when a line you score points to it)
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
