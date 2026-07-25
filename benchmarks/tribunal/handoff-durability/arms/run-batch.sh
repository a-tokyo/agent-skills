#!/usr/bin/env bash
# run-batch.sh [concurrency] [tag]
#
# Runs the full before/after matrix — v002 and v003 across haiku (n=3), sonnet (n=3) and
# opus (n=1), 14 captures — scores each with check.mjs, and writes one row per run to
# results/metrics.csv plus a per-cell summary to stdout.
#
# Scoring policy, matching the sibling harnesses:
#   exit 75 from run-arm.sh  -> env failure. Retried ONCE; never scored either way.
#   exit 3  from run-arm.sh  -> extraction failure. NOT scored; the transcript is kept so
#                               the parser can be fixed and replayed offline.
#   anything else            -> scored, as long as the capture has doer.txt + doer-report.txt.
#                               A non-zero claude exit (e.g. --max-turns) still produces a
#                               usable dispatch, and refusing to score it would silently
#                               drop the slowest runs and bias the result.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"

CONCURRENCY="${1:-3}"
TAG="${2:-batch}"
RESULTS="$BENCH/results/metrics.csv"
mkdir -p "$BENCH/results"
[ -f "$RESULTS" ] || echo "run_id,arm,model,status,doer_materialize_instruction,artifact_address_reported,verifier_count,verifier_address_propagation,budget_carried,handoff_durability" > "$RESULTS"

# one capture + score; appends a single CSV row
run_one() {
  local arm="$1" model="$2" n="$3"
  local run_id="${TAG}-${arm}-${model}-${n}"
  local out rc

  out="$("$HERE/run-arm.sh" "$arm" "$model" "$run_id" 2>&1)"; rc=$?
  if [ "$rc" -eq 75 ]; then
    echo "# $run_id: env failure, retrying once" >&2
    run_id="${run_id}r"
    out="$("$HERE/run-arm.sh" "$arm" "$model" "$run_id" 2>&1)"; rc=$?
    if [ "$rc" -eq 75 ]; then
      echo "$run_id,$arm,$model,env_failure,,,,,," >> "$RESULTS"
      return 0
    fi
  fi
  if [ "$rc" -eq 3 ]; then
    echo "$run_id,$arm,$model,extract_failure,,,,,," >> "$RESULTS"
    echo "# $run_id: EXTRACTION FAILED — not scored" >&2
    return 0
  fi

  local dispatch
  dispatch="$(ls -d "$BENCH/runs/$run_id/dispatched" 2>/dev/null)"
  if [ -z "$dispatch" ] || [ ! -f "$dispatch/doer.txt" ] || [ ! -f "$dispatch/doer-report.txt" ]; then
    echo "$run_id,$arm,$model,no_capture,,,,,," >> "$RESULTS"
    echo "# $run_id: no usable capture — not scored" >&2
    return 0
  fi

  local m
  m="$(node "$BENCH/check.mjs" "$dispatch" 2>&1)"
  get() { echo "$m" | sed -n "s/^METRIC $1=\(.*\)$/\1/p" | head -1; }
  echo "$run_id,$arm,$model,scored,$(get doer_materialize_instruction),$(get artifact_address_reported),$(get verifier_count),$(get verifier_address_propagation),$(get budget_carried),$(get handoff_durability)" >> "$RESULTS"
  echo "# $run_id: handoff_durability=$(get handoff_durability)" >&2
}

echo "# batch $TAG: 14 captures, concurrency $CONCURRENCY" >&2
for arm in v002 v003; do
  for spec in "haiku 3" "sonnet 3" "opus 1"; do
    set -- $spec; model="$1"; reps="$2"
    for n in $(seq 1 "$reps"); do
      while [ "$(jobs -rp | wc -l)" -ge "$CONCURRENCY" ]; do wait -n 2>/dev/null || sleep 5; done
      run_one "$arm" "$model" "$n" &
    done
  done
done
wait

echo
echo "# ---- per-cell summary (pass = handoff_durability 1) ----"
node - "$RESULTS" <<'NODE'
const rows = require("fs").readFileSync(process.argv[2], "utf8").trim().split("\n").slice(1)
  .map(l => l.split(","));
const cells = {};
for (const r of rows) {
  const [run_id, arm, model, status, dmi, aar, vc, vap, bc, hd] = r;
  const k = `${arm}/${model}`;
  cells[k] ??= { n: 0, dmi: 0, aar: 0, vap: 0, bc: 0, hd: 0, skipped: 0 };
  if (status !== "scored") { cells[k].skipped++; continue; }
  cells[k].n++;
  cells[k].dmi += +dmi; cells[k].aar += +aar; cells[k].vap += +vap;
  cells[k].bc += +bc; cells[k].hd += +hd;
}
console.log("cell            n  materialize  address  propagated  budget  PASS  skipped");
for (const [k, c] of Object.entries(cells)) {
  const f = (x) => `${x}/${c.n}`.padEnd(11);
  console.log(
    k.padEnd(15), String(c.n).padEnd(2), f(c.dmi), f(c.aar).slice(0,8).padEnd(8),
    f(c.vap).padEnd(11), f(c.bc).padEnd(7), f(c.hd).padEnd(5), c.skipped);
}
NODE
