#!/usr/bin/env bash
# evaluate.sh -- deterministic parity scoring for the database-docs skill.
#
# This owns the REPEATABLE half of the procedural eval: extract ground truth from the live DB and score a
# candidate schema.json against it -> METRIC lines. The LLM agent-run that PRODUCES the candidate is driven
# separately (orchestrator spawns a fresh agent with the skill). Keeping the agent-run out of this script
# makes the metric deterministic and fast, and avoids flaky headless-CLI dependencies in the inner loop.
#
# Usage: ./evaluate.sh <target-id> <candidate-schema.json>
#   target-id           -> targets/<target-id>.env  (DSN, dialect, schemas)
#   candidate-schema.json -> the schema.json a skill/agent produced
#
# Env: TRUTH_CACHE=1 reuses a previously extracted truth file for the target (faster inner loop).

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:?usage: evaluate.sh <target-id> <candidate-schema.json>}"
CANDIDATE="${2:?usage: evaluate.sh <target-id> <candidate-schema.json>}"

ENVFILE="$HERE/targets/$TARGET.env"
[ -f "$ENVFILE" ] || { echo "no target config: $ENVFILE" >&2; exit 2; }
# shellcheck disable=SC1090
set -a; source "$ENVFILE"; set +a

[ -f "$CANDIDATE" ] || { echo "candidate not found: $CANDIDATE" >&2; exit 2; }

# truth is written OUTSIDE any repo working tree so an agent under test can never read the oracle.
TRUTH_DIR="${TMPDIR:-/tmp}/database-docs-truth"
mkdir -p "$TRUTH_DIR"
TRUTH="$TRUTH_DIR/$TARGET.truth.json"

extract() {
  case "$DIALECT" in
    postgres) DATABASE_URL="$DATABASE_URL" node "$HERE/extract-pg.mjs" --schemas "${SCHEMAS:-public}" ;;
    mssql)    DATABASE_URL="$DATABASE_URL" node "$HERE/extract-mssql.mjs" --schemas "${SCHEMAS:-dbo}" ;;
    *) echo "unsupported dialect: $DIALECT" >&2; exit 2 ;;
  esac
}

if [ "${TRUTH_CACHE:-0}" = "1" ] && [ -s "$TRUTH" ]; then
  echo "# using cached truth: $TRUTH" >&2
else
  echo "# extracting ground truth ($DIALECT) ..." >&2
  extract > "$TRUTH.tmp"
  # determinism gate: extract again, require byte-identical
  extract > "$TRUTH.tmp2"
  if ! diff -q "$TRUTH.tmp" "$TRUTH.tmp2" >/dev/null; then
    echo "FATAL: oracle non-deterministic for $TARGET" >&2; exit 3
  fi
  mv "$TRUTH.tmp" "$TRUTH"; rm -f "$TRUTH.tmp2"
fi

node "$HERE/score.mjs" "$TRUTH" "$CANDIDATE"
