# Evaluation Templates

Skeletons for the 3.4 evaluation scripts. Read it only when writing an LLM-judge `evaluate.sh`, a multi-judge
`judges.yaml` or an `evaluate-checks.sh`; the contract they implement is phase-3.md 3.4. A worked judge call with its
METRIC lines: `self-test/evaluation/evaluate.sh` in the [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness).

## Contents
- evaluate.sh Contract (skeleton; instruction, pipeline and code-behavior skills)
- Multi-Judge Configuration
- evaluate-checks.sh Example
- LLM-as-Judge Prompt Template

## evaluate.sh Contract

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
# judge (recommended: a different model family, as for the panel).
# Fallback: deterministic-only scoring if no LLM API configured

set -euo pipefail

# No argument: score each training case, then emit the mean of every METRIC over the cases
# (each dimension, then overall_score, the primary metric)
if [ $# -eq 0 ]; then
  awk '/^training:/{f=1;next} /^[^ #]/{f=0} f && /^ *- /{gsub(/^ *- *"?|"? *(#.*)?$/,""); print}' \
    work/evaluation/data-split.yaml \
  | while read -r case_path; do "$0" "$case_path" | grep '^METRIC '; done \
  | awk -F'[ =]' '{s[$2]+=$3; n[$2]++} END {if (!n["overall_score"]) exit 1
      for (k in s) if (k != "overall_score") printf "METRIC %s=%.4f\n", k, s[k]/n[k]
      printf "METRIC overall_score=%.4f\n", s["overall_score"]/n["overall_score"]}'
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

The specific approach depends on the skill type:
- **Instruction skills**: Call LLM with SKILL.md as system prompt (most common)
- **Pipeline skills**: Run the pipeline script, compare output artifacts to reference
- **Code-behavior skills**: Run the skill on a coding task, compare output to reference solution

## Multi-Judge Configuration

When using multiple LLM judges for higher confidence scoring:

```yaml
# work/evaluation/judges.yaml
judges:
  - model: "claude-opus-5-5"     # a strong judge; the newest ids today: claude-opus-5-5, claude-sonnet-5-5, claude-haiku-5-5
    weight: 1.0
  - model: "<model-2>"           # a different model family from the builder
    weight: 1.0
  - model: "claude-haiku-5-5"    # optional third judge
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

## evaluate-checks.sh Example

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

## LLM-as-Judge Prompt Template

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
