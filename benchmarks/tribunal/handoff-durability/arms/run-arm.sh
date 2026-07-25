#!/usr/bin/env bash
# run-arm.sh <arm> <model> <run-id>
#
#   arm     v002 | v003     the tribunal SKILL.md version — the ONLY variable
#   model   haiku | sonnet | opus
#   run-id  unique id; everything lands in runs/<run-id>/
#
# Launches ONE fresh `claude -p` tribunal run in runs/<run-id>/work/ under full containment:
# isolated HOME + GIT_CONFIG_GLOBAL + XDG dirs so a --dangerously-skip-permissions agent can never
# read or write the maintainer's real config, credentials, or caches. Containment pattern and its
# hard-won details are inherited from ../../create-skill-autoresearch/arms/run-arm.sh — read that
# file's comments before changing anything here.
#
# Both arms A/B the SAME skill under the SAME prompt; v002 comes from `git show origin/main`, v003
# from the working tree, and each is copied to $HOME/.claude/skills/tribunal inside the fake HOME.
# User-level skills in the isolated HOME are the only placement Claude Code reliably discovers —
# do NOT reach for --append-system-prompt-file instead, it does not get the skill loaded.
#
# Scoring is NOT done here — extract + check afterwards (the command is printed at the end):
#   node ../check.mjs runs/<run-id>/dispatched
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$BENCH/../../.." && pwd)"

ARM="${1:?usage: run-arm.sh <v002|v003> <model> <run-id>}"
MODEL_ALIAS="${2:?missing model}"
RUN_ID="${3:?missing run-id}"

# Pinned full model ids, not bare aliases: `--model opus` follows whatever is current and would
# silently change tier between capture batches. Re-verify before each batch — the sibling
# harnesses still pin claude-opus-4-8, which is a generation stale.
case "$MODEL_ALIAS" in
  haiku)  MODEL_ID="claude-haiku-4-5-20251001" ;;
  sonnet) MODEL_ID="claude-sonnet-5" ;;
  opus)   MODEL_ID="claude-opus-5" ;;
  *) echo "unknown model alias: $MODEL_ALIAS" >&2; exit 2 ;;
esac
case "$ARM" in v002|v003) ;; *) echo "unknown arm: $ARM (expected v002|v003)" >&2; exit 2 ;; esac

TIMEOUT=2400
MAX_TURNS=120

# auth precheck BEFORE any run-dir creation (containment hides the interactive login; a headless
# credential is required: CLAUDE_CODE_OAUTH_TOKEN via `claude setup-token`, or ANTHROPIC_API_KEY).
# Keychain credentials do NOT survive the fake HOME.
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$BENCH/.auth-token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$BENCH/.auth-token")"; export CLAUDE_CODE_OAUTH_TOKEN
fi
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "run-arm.sh: no CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY (and no .auth-token file); aborting" >&2
  exit 2
fi

# Run dirs live OUTSIDE any git repo (the work dir gets its own `git init`; git probes must not
# walk up into the parent repo and find this one instead).
RUNS_ROOT="${BENCH_RUNS_ROOT:-${TMPDIR:-/tmp}/tribunal-handoff-bench}"
RUN="$RUNS_ROOT/$RUN_ID"
WORK="$RUN/work"
[ -e "$RUN" ] && { echo "run dir already exists: $RUN (run-ids are one-shot)" >&2; exit 2; }
mkdir -p "$WORK" "$RUN/home" "$BENCH/runs"
ln -sfn "$RUN" "$BENCH/runs/$RUN_ID"
export GIT_CEILING_DIRECTORIES="$RUNS_ROOT"

# ---------- containment: isolated HOME/GIT_CONFIG_GLOBAL/XDG ----------
export HOME="$RUN/home"
export XDG_CONFIG_HOME="$RUN/home/.config"
export XDG_CACHE_HOME="$RUN/home/.cache"
export XDG_DATA_HOME="$RUN/home/.local/share"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME"
export GIT_CONFIG_GLOBAL="$RUN/home/.gitconfig"
cat > "$GIT_CONFIG_GLOBAL" <<'EOF'
[user]
	name = tribunal-benchmark
	email = tribunal-benchmark@localhost
[init]
	defaultBranch = main
EOF

# ---------- arm setup: inject exactly one tribunal version into the fake HOME ----------
mkdir -p "$HOME/.claude/skills"
case "$ARM" in
  v003)
    # the shipped working-tree skill — docs/benchmarking.md requires the arm read the real file
    cp -R "$REPO/skills/tribunal" "$HOME/.claude/skills/tribunal"
    ;;
  v002)
    # the published baseline, straight out of the object store (worktrees share it)
    STAGE="$RUN/stage-v002"; mkdir -p "$STAGE"
    git -C "$REPO" archive origin/main skills/tribunal | tar -x -C "$STAGE"
    cp -R "$STAGE/skills/tribunal" "$HOME/.claude/skills/tribunal"
    ;;
esac
INJECTED_VERSION="$(sed -n 's/^version: *//p' "$HOME/.claude/skills/tribunal/SKILL.md" | head -1)"
echo "# arm $ARM -> injected tribunal SKILL.md version: ${INJECTED_VERSION:-<none>}" >&2

# ---------- task setup: the slice spec in a real git repo ----------
# git init is load-bearing: without a reachable commit address the v003 arm would fail for the
# wrong reason (the doer cannot materialize what it cannot commit).
cp "$BENCH/task/spec.md" "$WORK/spec.md"
git -C "$WORK" init -q
git -C "$WORK" add spec.md
git -C "$WORK" -c commit.gpgsign=false commit -q -m "slice spec"

# ---------- render the prompt (everything after the '---' rule in run-prompt.md) ----------
awk 'f{print} /^---$/{f=1}' "$BENCH/task/run-prompt.md" | sed '/./,$!d' > "$RUN/prompt.txt"
[ -s "$RUN/prompt.txt" ] || { echo "empty prompt extracted from task/run-prompt.md" >&2; exit 2; }

# ---------- run the agent ----------
START_TS="$(date +%s)"
set +e
# stdin MUST be /dev/null: an inherited empty stdin makes the CLI wait, warn, and the agent can
# read the hiccup as a user interjection and pause mid-run.
# env MUST be scrubbed (env -i + allowlist): a --dangerously-skip-permissions agent can printenv,
# so the caller's unrelated secrets must never reach the session.
# stream-json + --verbose is what extract-dispatch.mjs parses; the raw transcript is kept so a
# parser fix can be replayed offline without re-spending the capture.
( cd "$WORK" && perl -e 'alarm shift @ARGV; exec @ARGV' -- "$TIMEOUT" env -i \
    PATH="$PATH" HOME="$HOME" TERM="${TERM:-dumb}" LANG="${LANG:-en_US.UTF-8}" SHELL="${SHELL:-/bin/bash}" \
    XDG_CONFIG_HOME="$XDG_CONFIG_HOME" XDG_CACHE_HOME="$XDG_CACHE_HOME" XDG_DATA_HOME="$XDG_DATA_HOME" \
    GIT_CONFIG_GLOBAL="$GIT_CONFIG_GLOBAL" GIT_CEILING_DIRECTORIES="$GIT_CEILING_DIRECTORIES" \
    CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    claude -p "$(cat "$RUN/prompt.txt")" \
    --model "$MODEL_ID" \
    --dangerously-skip-permissions \
    --max-turns "$MAX_TURNS" \
    --output-format stream-json --verbose \
  ) <"/dev/null" >"$RUN/transcript.jsonl" 2>"$RUN/stderr.txt"
EXIT_CODE=$?
set -e
END_TS="$(date +%s)"
WALL=$((END_TS - START_TS))

# ---------- env-failure detection (policy: rerun once, never score an env_failure) ----------
ENV_FAILURE=0
# "session limit" / rate_limit is checked REGARDLESS of exit code and even on exit 0: a run
# can dispatch a doer, exhaust the account allowance, and stop early with a partial capture
# that scores as a clean FAIL. That happened on the first batch — two runs were scored 0
# after dying in ~2 minutes — which is precisely the never-score-an-env-failure policy being
# defeated by a detector that did not know the failure existed.
if grep -qiE "you've hit your (session|usage) limit|session limit ·|\"rate_limit\"|rate_limit_event|usage limit reached" \
    "$RUN/transcript.jsonl" "$RUN/stderr.txt" 2>/dev/null; then
  ENV_FAILURE=1
fi
if [ "$ENV_FAILURE" = "0" ] && [ "$EXIT_CODE" -ne 0 ]; then
  if grep -qiE 'ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|network error|socket hang up|503 Service|429 Too Many|fetch failed|API Error: Connection closed|overloaded_error|Not logged in|Invalid API key' \
      "$RUN/transcript.jsonl" "$RUN/stderr.txt" 2>/dev/null; then
    ENV_FAILURE=1
  fi
fi

# ---------- capture the dispatch ----------
DISPATCH="$RUN/dispatched"
EXTRACT_OK=0
if [ "$ENV_FAILURE" = "0" ]; then
  if node "$BENCH/extract-dispatch.mjs" "$RUN/transcript.jsonl" "$DISPATCH" 2>>"$RUN/extract.log"; then
    EXTRACT_OK=1
  fi
fi
# a durable ledger the run wrote itself counts as carrying the budget; the orchestrator's closing
# summary does NOT, so only files under .tribunal/ are copied.
if [ -d "$WORK/.tribunal" ]; then
  mkdir -p "$DISPATCH"
  cat "$WORK/.tribunal"/*.md "$WORK/.tribunal"/*.txt > "$DISPATCH/ledger.md" 2>/dev/null || true
  [ -s "$DISPATCH/ledger.md" ] || rm -f "$DISPATCH/ledger.md"
fi

# ---------- meta + results row ----------
printf '{ "run_id": "%s", "arm": "%s", "skill_version": "%s", "model": "%s", "model_id": "%s", "exit_code": %s, "wall_seconds": %s, "env_failure": %s, "extract_ok": %s }\n' \
  "$RUN_ID" "$ARM" "$INJECTED_VERSION" "$MODEL_ALIAS" "$MODEL_ID" "$EXIT_CODE" "$WALL" "$ENV_FAILURE" "$EXTRACT_OK" > "$RUN/meta.json"
RESULTS="$BENCH/results/runs.csv"
mkdir -p "$BENCH/results"
[ -f "$RESULTS" ] || echo "run_id,arm,skill_version,model,exit_code,wall_seconds,env_failure,extract_ok" > "$RESULTS"
echo "$RUN_ID,$ARM,$INJECTED_VERSION,$MODEL_ALIAS,$EXIT_CODE,$WALL,$ENV_FAILURE,$EXTRACT_OK" >> "$RESULTS"

echo "# run $RUN_ID finished: exit=$EXIT_CODE wall=${WALL}s env_failure=$ENV_FAILURE extract_ok=$EXTRACT_OK" >&2
if [ "$ENV_FAILURE" = "0" ] && [ "$EXTRACT_OK" = "0" ]; then
  # Do NOT score this run. A half-extracted capture makes check.mjs report a missing file,
  # which is indistinguishable from the skill having failed — see extract-dispatch.mjs.
  echo "# EXTRACTION FAILED — do not score this run. Transcript kept at $RUN/transcript.jsonl;" >&2
  echo "#   fix extract-dispatch.mjs and re-run it offline (see $RUN/extract.log)." >&2
  exit 3
fi
echo "# check: node $BENCH/check.mjs $DISPATCH" >&2
[ "$ENV_FAILURE" = "1" ] && exit 75
exit "$EXIT_CODE"
