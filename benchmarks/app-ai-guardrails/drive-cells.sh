#!/usr/bin/env bash
# drive-cells.sh <lane-name> <cell>...
#   cell format: arm:model:stack:pm:n   e.g. bare:sonnet:next:npm:5
#
# Runs each cell's n runs sequentially: run-arm.sh -> evaluate.sh -> append results/scores.tsv.
# exit 75 (env_failure) from run-arm.sh -> rerun once with -r suffix; a second env failure is
# recorded as env_failure and NOT scored (benchmark policy). All other exits are scored as-is
# (a failed agent run is an honest result, not an environment problem).
#
# Auth: reads .auth-token (CLAUDE_CODE_OAUTH_TOKEN) if the env var is not already set.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANE="${1:?usage: drive-cells.sh <lane-name> <arm:model:stack:pm:n>...}"; shift

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -f "$HERE/.auth-token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$HERE/.auth-token")"
  export CLAUDE_CODE_OAUTH_TOKEN
fi

SCORES="$HERE/results/scores.tsv"
mkdir -p "$HERE/results"
[ -f "$SCORES" ] || printf 'run_id\tarm\tmodel\tstack\tpm\tguardrail_score\tall_gates_pass\tstatus\n' > "$SCORES"

score_run() { # <run-id> <arm> <model> <stack> <pm>
  local id="$1" arm="$2" model="$3" stack="$4" pm="$5"
  local mfile="$HERE/runs/$id/metrics.txt"
  if "$HERE/evaluate.sh" "$HERE/runs/$id/work" > "$mfile" 2> "$HERE/runs/$id/evaluate.err"; then
    local gs agp
    gs="$(awk -F= '/^METRIC guardrail_score=/{print $2}' "$mfile" | tail -1)"
    agp="$(awk -F= '/^METRIC all_gates_pass=/{print $2}' "$mfile" | tail -1)"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\tscored\n' "$id" "$arm" "$model" "$stack" "$pm" "${gs:-NA}" "${agp:-NA}" >> "$SCORES"
    echo "[$LANE] scored $id: guardrail_score=${gs:-NA} all_gates_pass=${agp:-NA}"
  else
    printf '%s\t%s\t%s\t%s\t%s\tNA\tNA\tevaluate_error\n' "$id" "$arm" "$model" "$stack" "$pm" >> "$SCORES"
    echo "[$LANE] EVALUATE ERROR $id (see runs/$id/evaluate.err)"
  fi
}

for CELL in "$@"; do
  IFS=: read -r ARM MODEL STACK PM N <<< "$CELL"
  for i in $(seq 1 "$N"); do
    ID="${ARM}-${MODEL}-${STACK}-${PM}-${i}"
    if [ -e "$HERE/runs/$ID" ]; then
      # resumability: skip any id that already has a row of ANY status (scored, env_failure,
      # invalid, ...) — re-scoring env-failed partial repos produced bogus rows (found 2026-07-04).
      if grep -q "^$ID	" "$SCORES" 2>/dev/null || grep -q "^$ID-r	" "$SCORES" 2>/dev/null; then
        echo "[$LANE] skip $ID (row exists)"; continue
      fi
      # never score a dir whose own run was classified env_failure
      if grep -q '"env_failure": true' "$HERE/runs/$ID/meta.json" 2>/dev/null; then
        echo "[$LANE] skip $ID (env_failure meta, unscored by policy)"; continue
      fi
      score_run "$ID" "$ARM" "$MODEL" "$STACK" "$PM"; continue
    fi
    echo "[$LANE] running $ID ..."
    "$HERE/run-arm.sh" "$ARM" "$MODEL" "$STACK" "$PM" "$ID" > /dev/null 2>&1
    RC=$?
    if [ "$RC" -eq 75 ]; then
      echo "[$LANE] env_failure on $ID -> rerun once"
      ID="${ID}-r"
      "$HERE/run-arm.sh" "$ARM" "$MODEL" "$STACK" "$PM" "$ID" > /dev/null 2>&1
      RC=$?
      if [ "$RC" -eq 75 ]; then
        printf '%s\t%s\t%s\t%s\t%s\tNA\tNA\tenv_failure\n' "$ID" "$ARM" "$MODEL" "$STACK" "$PM" >> "$SCORES"
        echo "[$LANE] $ID env_failure twice -> recorded, not scored"; continue
      fi
    fi
    score_run "$ID" "$ARM" "$MODEL" "$STACK" "$PM"
  done
done
echo "[$LANE] lane complete"
