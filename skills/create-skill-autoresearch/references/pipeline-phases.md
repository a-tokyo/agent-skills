# Pipeline Phases Reference

Detailed instructions for each phase of the create-skill-autoresearch factory.

## Contents

- Phase 1: Interview -- Detailed Question Flow
- Phase 2: Research -- Subagent Orchestration
- Phase 3: Draft -- Design-First Approach (DESIGN.md template, `evaluate.sh` contract, judge templates, plugin-eval worked case, gate runner)
- Phase 4: Autoresearch -- Integration Details (invocation, loop conventions, data split, overfitting detection)
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

## Invocation & Hierarchy
- Invocation mode: <model-invoked | user-invoked> and why (from INVOCATION_MODE)
- Information hierarchy: <what is a step vs reference; what stays inline vs disclosed,
  and which branch licenses each disclosure>

## Leading Words
- <candidate word>: <where it works -- body (execution) or description (invocation)>

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
# Usage: ./evaluate.sh                    # the current training split (Phase 4 metric command)
#        ./evaluate.sh <test-case-path>   # one case
# Output: METRIC lines to stdout
#
# Contract:
# 1. No argument: scores every case in the `training` list of work/evaluation/data-split.yaml
#    and emits their mean as overall_score; with a case path, scores that one case
# 2. Extracts input from the test case
# 3. Calls an LLM with SKILL.md as system prompt + test input as user message
# 4. Captures the LLM output
# 5. Runs deterministic checks (line count, frontmatter, links) → emit METRIC lines
# 6. Sends output + reference to an LLM judge for subjective dimensions → emit METRIC lines
# 7. Computes and emits METRIC overall_score=<weighted_average>
#
# Requirements: JUDGE_API_BASE, JUDGE_API_KEY, JUDGE_MODEL env vars for LLM steps.
# Optional: EXECUTOR_MODEL to run the skill-under-test on a *different* model from the
# judge (recommended -- see SKILL.md Phase 3.4: use a different model family for the panel).
# Fallback: deterministic-only scoring if no LLM API configured

set -euo pipefail

# No argument: score each training case, then emit the mean as the primary metric
if [ $# -eq 0 ]; then
  awk '/^training:/{f=1;next} /^[^ #]/{f=0} f && /^ *- /{gsub(/^ *- *"?|"? *(#.*)?$/,""); print}' \
    work/evaluation/data-split.yaml \
  | while read -r case_path; do "$0" "$case_path" | grep '^METRIC overall_score='; done \
  | awk -F= '{s+=$2; n++} END {if (!n) exit 1; printf "METRIC overall_score=%.4f\n", s/n}'
  exit
fi

TEST_CASE="$1"
RUBRIC="work/evaluation/rubric.yaml"
SKILL="output/<skill-name>/SKILL.md"

# Step 1-2: Extract input
INPUT=$(cat "$TEST_CASE")
REFERENCE=$(cat "$(dirname "$TEST_CASE")/$(basename "$TEST_CASE" .md | sed 's/input/output/').md")

# Step 3-4: Invoke skill via LLM API (curl to OpenAI-compatible endpoint).
# Build the JSON body with a real encoder -- never string-interpolate file contents into
# JSON (quotes/newlines in the skill or the input would otherwise produce invalid JSON).
REQUEST=$(SKILL_PATH="$SKILL" USER_INPUT="$INPUT" MODEL="${EXECUTOR_MODEL:-$JUDGE_MODEL}" python3 -c '
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

`self-test/evaluation/evaluate.sh` in the [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness) repo shows the judge call and the METRIC lines for one case.

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
# Correctness gate -- must pass before metric measurement; no argument checks the skill being built
set -euo pipefail
SKILL="${1:-output/<skill-name>/SKILL.md}"
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

### Plugin-Eval Worked Case (SKILL.md 3.6)

One gate case (a test-split gold standard, or any gold standard with 3-9) for a skill that reads input files and writes `po.json`. Each run starts in an
empty workspace; the scaffold copies the case's fixtures in. Inside the wrapper plugin:

```
work/evaluation/plugin/
  .claude-plugin/plugin.json      # {"name": "<skill-name>-eval", "version": "0.0.0"}
  skills/<skill-name>/            # copy of output/<skill-name>/, refreshed before every run
  evals/
    holdout-03/
      prompt.md
      case.yaml
      fixture.sh                  # chmod +x
      fixtures/request.md  fixtures/master-data/...
      graders/approval.md  graders/skill-fired.md
```

```bash
# fixture.sh — runs in the empty workspace; locate the case dir from the script's own path
#!/usr/bin/env bash
set -euo pipefail
cp -R "$(dirname "$0")/fixtures/." .
```

```yaml
# case.yaml
schema_version: "1.1"
name: holdout-03
context:
  scaffold_script: fixture.sh
```

```markdown
---
max_turns: 20
timeout_seconds: 400
allowed_tools: [Read, Glob, Grep, Bash, Write, Skill]
---

Turn request.md into a purchase order using the master data in master-data/, and write it to po.json.
```

```yaml
# graders/approval.md — grade the file the skill wrote, not the chat
---
type: regex
target: { source: file, path: po.json }
pattern: "\"approval\":\\s*\"director\""
---
```

```yaml
# graders/skill-fired.md — a with-arm indicator, excluded from the with/without score; approval.md is
# score-bearing, so holdout-03 stays a gold case (only a case graded by nothing but Skill is an indicator)
---
type: tool_used
tool: Skill
---
```

```yaml
# evals/near-miss/graders/not-fired.md — the should-not-trigger case: the skill must not fire in either arm
---
type: tool_used
tool: Skill
min: 0
max: 0
arm: both
---
```

### Gate Runner (SKILL.md 5.0)

`scripts/gate.py` is the only writer of the gate artifact, and it always runs as the owned job `gate`. A
full gate can take over an hour (default plugin-eval timeout 90 minutes), longer than any agent shell call
waits, so an agent starts it detached and polls with short calls:

```bash
python3 <skill-dir>/scripts/gate.py --detach --model <model> …       # or: run_owned.py start gate -- python3 <skill-dir>/scripts/gate.py --model <model> …
python3 <skill-dir>/scripts/run_owned.py wait gate --timeout 540      # exits 124 while it runs; repeat until it exits with the gate's code
```

A second start while the job is live is refused with these commands. `gate.py --restart …` stops only the
recorded `gate` group and starts it again, detached; `gate.py --stop` stops it. On TERM the gate stops the
eval runs it started (each in its own process group) before it exits, and leaves `gate.json` unfinished, so
a stopped gate never validates. Without `--detach`, `gate.py` starts the job and waits for it, which suits a
human at a terminal; if that call is cut off the job keeps running.

In plugin mode it refreshes `work/evaluation/plugin/skills/<skill-name>/` from `output/`, checks that
`evals/` holds exactly the cases recorded under `plugin_eval`, and runs this from
`work/evaluation/plugin/` as its own owned job:

```bash
claude plugin eval . --ablation with-without --runs 3 -j 4 --model <model> --allow-tools Bash Write \
  --scaffold --trust-plugin --no-publish --threshold 0 --json <workspace>/work/evaluation/plugin-eval.json
```

`--scaffold` runs each case's `scaffold_script` (without it the fixtures never arrive), `--allow-tools`
grants the gated tools `prompt.md` lists, `--trust-plugin` answers the first-run trust prompt in unattended
runs, `--threshold 0` keeps a low score from failing the run (the verdict reads Δ, not the exit code), and
`--no-publish` keeps the report local. An interrupted or cost-capped run writes `"partial": true`.

In manual mode it calls `work/evaluation/run-case.sh <case> <with|without>` 3 times per arm per case
(`--jobs` at once, default 4), each run in its own process group with a per-run timeout (default 20
minutes), retries a failed run once, and writes `uplift.json`; a second failure leaves it `"partial": true`
with an `errors` list. Both modes record `work/evaluation/gate.json` (mode, probe exit code, cases, and
sha256 hashes of `output/<skill-name>/` and of the case set — names plus each case's `evals/` directory;
interpreter caches excluded — and, when the run finishes, of `run-case.sh`, every file under `input/`,
the scorers and fixtures under `work/evaluation/` (not `plugin/`, gate outputs, dotfiles or
`*.tmp`/`*.out`/`*.log` scratch) and the artifact itself), move a previous artifact to `<name>.prev.json`
first so a stale file can never pass, and end by running `check_gate.py`. It exits 0 only when `gate.json`
records a finished run whose hashes still match, and the artifact says `"partial": false`, covers exactly
the recorded cases and has at least 3 runs per arm. Any edit after the gate fails it: "skill changed since
the gate; re-run 5.0" for the skill or suite, "gate inputs changed since the gate; re-run 5.0" for the
adapter, a scorer, a fixture or the artifact. A case is gold when any of its graders is score-bearing (anything but `tool_used` on
`Skill`), even if it also carries a Skill indicator; every gold case needs a numeric Δ, and when
`data-split.yaml` records gold standards a gate with no gold case fails (`no_gold`). Its `GATE VALID:`
line gives the mean Δ and the Δ <= 0 list over gold cases only; should-trigger cases (graded only by
`tool_used` on `Skill`) report how many with-arm runs fired the skill, and near misses (`max: 0`) how
many runs in both arms left it quiet. Smoke-test the suite with `gate.py --runs 1` before the full gate: a with-arm score
of 0 while `skill-fired` passes means the skill fired but could not act — check the tool grants and the
fixtures first. The 1-run artifact never passes `check_gate.py`; the full run replaces it.

---

## Phase 4: Autoresearch -- Integration Details

### Preparing the Autoresearch Invocation

The factory runs the upstream **autoresearch** skill (github/awesome-copilot) and answers its
interactive setup from these inputs, so the loop never waits on a user:

1. **Goal statement**: "Improve <skill-name> quality as measured by `overall_score` against gold standards"
2. **Metric command**: `./work/evaluation/evaluate.sh` with no argument — scores the current `training`
   split of `work/evaluation/data-split.yaml` (must emit METRIC lines)
3. **Metric extraction**: regex `^METRIC overall_score=([0-9.]+)$` on `run.log`
4. **Direction**: `higher_is_better`
5. **In-scope files**: only the skill being built (`output/<skill-name>/SKILL.md`, `references/*`, `scripts/*`)
6. **Out-of-scope**: everything else (`input/` and `work/` — evaluation scripts, gold standards, research)
7. **Budget**: `rubric.yaml` `max_iterations`
8. **Constraints**: `./work/evaluation/evaluate-checks.sh`, if it exists, exits 0 before MEASURE

### Loop Conventions the Factory Owns

Upstream autoresearch keeps `results.tsv` and `run.log` and reverts with `git reset`. The factory
adds the rest, and applies it in every experiment:

- **Scoped commits.** Stage only in-scope paths (`git add output/<skill-name>`). Never `git add -A` —
  it sweeps `work/` and session logs into experiment commits.
- **Repository guard.** Before any `git reset --hard` or `git clean`, assert that
  `git rev-parse --show-toplevel` equals the workspace root, and refuse to revert otherwise. A
  workspace nested in a parent project (a gitignored `builds/`, say) would reset the parent's last
  commit and wipe its uncommitted work; SKILL.md 1.6 makes the workspace its own repository.
- **Owned processes.** Start every background job (eval runs, `until` loops) with
  `scripts/run_owned.py start <name> -- <cmd...>`, and wait on or stop it with `wait <name>` /
  `stop <name>`: it stops only the process group it recorded, because on a shared machine a
  name-pattern kill also takes down sibling builds and the user's own processes. It refuses to signal
  a job whose recorded start time it cannot confirm, and keeps `work/pids/` out of git.
- **Clean reverts.** Discard with `git reset --hard HEAD~1 && git clean -fd -- output/<skill-name>`,
  so a reverted experiment leaves no new untracked files behind. The baseline commit (SKILL.md 3.5) is
  experiment 0, so `HEAD~1` always exists.
- **Noise threshold.** Keep a change only if `overall_score` beats the best by more than the judge
  noise threshold: the larger of 0.01 absolute minimum and the per-judge variance measured on the
  baseline (re-run the baseline twice to estimate it). Gains inside the noise are discards.
- **Plateau window.** `rubric.yaml` `plateau_window` (default 5) consecutive discards is a plateau;
  SKILL.md 4.3 decides whether it is terminal.
- **Baseline failure.** If the metric command fails on experiment 0 (no `METRIC overall_score` line,
  non-zero exit), stop and fix `evaluate.sh` — never start the loop against a broken metric.
- **Experiment record.** Append one line to `autoresearch.jsonl` per experiment:

  ```json
  { "experiment": 7, "commit": "abc1234", "metric": 0.81, "status": "keep",
    "scores": { "clarity": 8.1 }, "hypothesis": "...", "learned": "...", "rollback_reason": null }
  ```

- **Context budget.** Read only the tail of `run.log` and the last few jsonl lines per THINK step;
  when the session's context runs low, hand off (SKILL.md 4.4) rather than compress history.

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

## Craft Passes (writing-for-agents; fallback: standalone-fallback.md)
- [ ] Leading-word hunt: collapse restated qualities and fuzzy gates into pretrained tokens
- [ ] Pruning pass: sentence-level no-op test, duplication and sediment sweep
- [ ] Disclosure rebalance: inline what every branch needs, disclose what only some reach
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
plugin_eval_available: 0  # exit code of `claude plugin eval --help`; non-zero → manual protocol (SKILL.md 5.0)
plugin_eval:              # every case 5.0 runs, by eval dir name: a list, not a mapping (check_gate.py reads it)
  - "holdout-03"
  - "trigger-1"
  - "near-miss"
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
training:            # this experiment's rotation: every case but the held-out one
  - "input/input-02.md"
  - "input/input-03.md"
```

Rewrite `training` to the next rotation in each LOG step, after the keep/revert decision, and commit
data-split.yaml on its own so a later experiment revert cannot roll the rotation back (SKILL.md 4.2);
`evaluate.sh` with no argument scores it.

**Overfitting detection for leave-one-out**: Since there is no fixed validation set, use per-case score variance as the signal:
- After each kept experiment, record per-case scores (not just the aggregate)
- If the variance across cases increases while the mean improves, the skill is specializing for some cases at the expense of others
- Flag when any single case drops by > 1.0 point while others improve
- Log per-case scores in `autoresearch.jsonl`: `"per_case_scores": {"input-01": 8.2, "input-02": 7.1, ...}`

### Handoff State Schema

`work/handoffs/state.yaml` — the structured resume state written on handoff (SKILL.md Phase 4.4):

```yaml
phase: autoresearch       # current phase (interview|research|draft|autoresearch|verify)
session: <N>              # session counter (increments on each resume)
skill_name: <name>        # the skill being built
best_score: <value>       # best overall_score achieved
best_commit: <hash>       # commit hash of best state
experiments_run: <count>  # total experiments across all sessions
remaining_budget: <count> # experiments left in budget
validation_score: <value> # last validation set score (if applicable)
top_concerns:             # panel feedback or known weaknesses
  - <concern 1>
  - <concern 2>
blocked_dimensions: []    # dimensions below threshold
last_updated: <ISO timestamp>
baseline_lock: false      # true = a human froze target_score at the recorded best; do not re-derive
```

`baseline_lock` exists so a run can be frozen deliberately rather than by interrupting the loop. When
it is true, Phase 4 treats `best_score` as satisfying the exit criteria and proceeds to Phase 5, and the
real target is recorded in `autoresearch.ideas.md` so the gap is not forgotten.

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
- For the predictability dimension: check every step ends on a checkable completion
  criterion (done vs not-done is decidable) and hunt no-op lines the model already obeys
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
7. Craft failure modes: premature completion, duplication, sediment, sprawl, no-op lines (writing-for-agents; fallback: standalone-fallback.md)

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
