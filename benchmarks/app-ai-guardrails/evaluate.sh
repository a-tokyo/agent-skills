#!/usr/bin/env bash
# evaluate.sh -- deterministic (no-LLM) guardrail scoring for a scaffolded repo.
#
# Usage: ./evaluate.sh <repo-dir> [--e2e]
#   <repo-dir>  a scaffolded app repo (bare or with-skill arm, golden or bare fixture, real run output)
#   --e2e       actually EXECUTE the e2e and audit gates (needs network/browser availability). Without
#               it, those two fall back to a deterministic "config/spec present" check (lib/config-present.mjs)
#               so the scorer works fully offline by default -- per DESIGN.md §8 handoff notes.
#
# Pipeline: detect stack + the 7 gate commands (lib/detect-gates.mjs) -> execute tier-1 gates, capturing
# exit codes -> hand the result to check-guardrails.mjs, which owns teeth probes, marker probes, and the
# final METRIC lines (including `guardrail_score` and `all_gates_pass`). This script never scores
# anything itself -- it only resolves and runs commands, so all rubric logic lives in one place.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The scorer MUST see the same toolchain the agents had, or it fails gates the repo actually passes
# (uniform capped-50 rust/springboot rows, 2026-07-06). Single source of truth with run-arm.sh:
BENCH_HERE="$HERE" source "$HERE/toolchain-env.sh"

REPO_ARG="${1:?usage: evaluate.sh <repo-dir> [--e2e]}"
shift || true
E2E=0
for a in "$@"; do
  if [ "$a" = "--e2e" ]; then E2E=1; fi
done

[ -d "$REPO_ARG" ] || { echo "no such repo dir: $REPO_ARG" >&2; exit 2; }
REPO="$(cd "$REPO_ARG" && pwd)"

# Project-root descent: agents legitimately scaffold into a named subdirectory (scaffolders default
# to <app-name>/; haiku does this, sonnet tends to scaffold in place — found 2026-07-04). The
# deliverable is the repo wherever it was created: if the given dir has no manifest but contains
# exactly ONE candidate subdir that does, descend. One level only; ambiguity (0 or >1) stays fatal
# at detection, exactly as before. Applies identically to both arms — no fairness skew.
has_manifest() { [ -e "$1/package.json" ] || [ -e "$1/pyproject.toml" ] || [ -e "$1/go.mod" ] || [ -e "$1/Cargo.toml" ] || [ -e "$1/build.gradle" ] || [ -e "$1/build.gradle.kts" ]; }
if ! has_manifest "$REPO"; then
  CANDIDATES=()
  for d in "$REPO"/*/; do
    [ -d "$d" ] || continue
    case "$(basename "$d")" in node_modules|.git|.claude|coverage|dist|build|target) continue ;; esac
    has_manifest "${d%/}" && CANDIDATES+=("${d%/}")
  done
  if [ "${#CANDIDATES[@]}" -eq 1 ]; then
    echo "# descending into project subdir: $(basename "${CANDIDATES[0]}")" >&2
    REPO="${CANDIDATES[0]}"
  fi
fi

DETECT_JSON="$(node "$HERE/lib/detect-gates.mjs" "$REPO")" || { echo "gate detection failed for $REPO" >&2; exit 2; }
STACK="$(DETECT_JSON="$DETECT_JSON" node -e 'process.stdout.write(JSON.parse(process.env.DETECT_JSON).stack)')"
echo "# stack=$STACK repo=$REPO e2e_mode=$([ "$E2E" = 1 ] && echo executed || echo fallback)" >&2

get_cmd() {
  # get_cmd <gate> -> resolved command string, or the literal text "null" if the gate is absent.
  DETECT_JSON="$DETECT_JSON" GATE="$1" node -e '
    const d = JSON.parse(process.env.DETECT_JSON);
    const c = d.gates[process.env.GATE];
    process.stdout.write(c == null ? "null" : c);
  '
}

# Accumulates one JSON object entry per gate; assembled into the final --gates blob at the end.
GATES_JSON="{}"
add_gate_result() {
  # add_gate_result <gate> <cmd-or-null> <exit-or-empty> <mode>
  local gate="$1" cmd="$2" exit_code="$3" mode="$4"
  GATES_JSON="$(GATES_JSON="$GATES_JSON" GATE="$gate" CMD="$cmd" EXITV="$exit_code" MODE="$mode" node -e '
    const d = JSON.parse(process.env.GATES_JSON);
    const cmd = process.env.CMD === "null" ? null : process.env.CMD;
    const exitv = process.env.EXITV === "" ? null : Number(process.env.EXITV);
    d[process.env.GATE] = { cmd, exit: exitv, mode: process.env.MODE };
    process.stdout.write(JSON.stringify(d));
  ')"
}

run_gate() {
  # run_gate <cmd> -> prints exit code to stdout (bash-safe capture); never aborts the script.
  local cmd="$1"
  set +e
  ( cd "$REPO" && eval "$cmd" ) >/dev/null 2>&1
  local code=$?
  set -e
  echo "$code"
}

for gate in lint typecheck test coverage build; do
  cmd="$(get_cmd "$gate")"
  if [ "$cmd" = "null" ]; then
    add_gate_result "$gate" "null" "" "missing"
    echo "# $gate: missing (no gate entry found)" >&2
  else
    code="$(run_gate "$cmd")"
    add_gate_result "$gate" "$cmd" "$code" "executed"
    echo "# $gate: executed \`$cmd\` exit=$code" >&2
  fi
done

for gate in e2e audit; do
  cmd="$(get_cmd "$gate")"
  if [ "$cmd" = "null" ]; then
    add_gate_result "$gate" "null" "" "missing"
    echo "# $gate: missing (no gate entry found)" >&2
  elif [ "$E2E" = "1" ]; then
    code="$(run_gate "$cmd")"
    add_gate_result "$gate" "$cmd" "$code" "executed"
    echo "# $gate: executed \`$cmd\` exit=$code" >&2
  else
    set +e
    node "$HERE/lib/config-present.mjs" "$REPO" "$STACK" "$gate"
    present_code=$?
    set -e
    add_gate_result "$gate" "$cmd" "$present_code" "fallback"
    echo "# $gate: config-present fallback -> exit=$present_code (cmd exists: \`$cmd\`, not executed)" >&2
  fi
done

node "$HERE/check-guardrails.mjs" "$REPO" --stack "$STACK" --gates "$GATES_JSON"
