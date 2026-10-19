#!/usr/bin/env bash
# run-and-score.sh <arm> <model> <run-id> [task] [executor-runs]
#
# One build (run-arm.sh) followed by its deterministic score; appends one row to results/scores.csv.
# executor-runs applies to task-2 only and defaults to 2 per holdout, the published method.
# env_failure runs (exit 75) are retried once under a "-retry" run-id and never scored.
# SCORE_ONLY=1 skips the build and scores an existing run dir (e.g. one whose scoring step failed).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"
ARM="$1"; MODEL="$2"; RUN_ID="$3"; TASK_NAME="${4:-task}"; EXEC_RUNS="${5:-2}"
RUNS_ROOT="${BENCH_RUNS_ROOT:-${TMPDIR:-/tmp}/create-skill-autoresearch-bench}"

if [ "${SCORE_ONLY:-0}" = "1" ]; then
  code="$(sed -n 's/.*"exit_code": \([0-9]*\).*/\1/p' "$RUNS_ROOT/$RUN_ID/meta.json")"
  # run-arm.sh exits 75 for an env failure and meta.json records it as env_failure: a re-score keeps
  # the exclusion (never scored) and exits 75 without starting the retry build.
  if grep -q '"env_failure": 1' "$RUNS_ROOT/$RUN_ID/meta.json"; then
    echo "# $RUN_ID: env_failure in meta.json, not scored" >&2
    exit 75
  fi
else
  "$HERE/run-arm.sh" "$ARM" "$MODEL" "$RUN_ID" "$TASK_NAME"
  code=$?
fi
if [ "$code" = "75" ]; then
  RUN_ID="$RUN_ID-retry"
  "$HERE/run-arm.sh" "$ARM" "$MODEL" "$RUN_ID" "$TASK_NAME"; code=$?
  [ "$code" = "75" ] && { echo "# $RUN_ID: env_failure twice, not scored" >&2; exit 75; }
fi

case "$TASK_NAME" in
  task)   OUT="conventional-commits"; SCORER="score.mjs"; EXTRA=() ;;
  task-2) OUT="purchase-order-builder"; SCORER="score-task2.mjs"; EXTRA=(--runs "$EXEC_RUNS") ;;
esac
RUN="$RUNS_ROOT/$RUN_ID"
BUILD_DIR="$RUN/ws"; [ -d "$BUILD_DIR" ] || BUILD_DIR="$RUN/work"  # run dirs from before the ws/ rename
node "$BENCH/scoring/$SCORER" "$BUILD_DIR/output/$OUT" --executor-out "$RUN" ${EXTRA[@]+"${EXTRA[@]}"} > "$RUN/score.txt" 2>&1
metric() { sed -n "s/^METRIC $1=//p" "$RUN/score.txt"; }
if [ -z "$(metric overall_score)" ]; then
  echo "# $RUN_ID: scoring failed, no row written — see $RUN/score.txt" >&2
  exit 1
fi
wall="$(sed -n 's/.*"wall_seconds": \([0-9]*\).*/\1/p' "$RUN/meta.json")"
note="exit=$code"; [ "${HOST:-claude}" = "claude" ] || note="$note host=$HOST"
echo "$RUN_ID,$ARM,$MODEL,$(metric compliance),$(metric craft),$(metric execution),$(metric overall_score),$wall,claude-cli,$note,$TASK_NAME" \
  >> "$BENCH/results/scores.csv"
echo "# $RUN_ID: overall=$(metric overall_score) execution=$(metric execution)" >&2
