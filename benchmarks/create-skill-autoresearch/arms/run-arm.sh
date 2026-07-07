#!/usr/bin/env bash
# run-arm.sh <arm> <model> <run-id>
#
#   arm     bare | skill-creator | factory
#   model   haiku | sonnet | opus   (builder model; the scorer's executor model is fixed separately)
#   run-id  unique id; everything lands in runs/<run-id>/
#
# Launches ONE fresh `claude -p` build run in runs/<run-id>/work/ under full containment:
# isolated HOME + GIT_CONFIG_GLOBAL + XDG dirs so a --dangerously-skip-permissions agent can never
# read or write the maintainer's real config, credentials, or caches. Pattern proven in the
# app-ai-guardrails benchmark harness (isolated-HOME skill injection: user-level ~/.claude/skills
# in the fake HOME is the only placement Claude Code reliably discovers).
#
# Scoring is NOT done here — run the scorer afterwards:
#   node ../scoring/score.mjs runs/<run-id>/work/output/conventional-commits
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$BENCH/../.." && pwd)"

ARM="${1:?usage: run-arm.sh <bare|skill-creator|factory> <model> <run-id>}"
MODEL_ALIAS="${2:?missing model}"
RUN_ID="${3:?missing run-id}"

case "$MODEL_ALIAS" in
  haiku)  MODEL_ID="claude-haiku-4-5-20251001" ;;
  sonnet) MODEL_ID="claude-sonnet-5" ;;
  opus)   MODEL_ID="claude-opus-4-8" ;;
  *) echo "unknown model alias: $MODEL_ALIAS" >&2; exit 2 ;;
esac
case "$ARM" in
  bare)          TIMEOUT=1200;  MAX_TURNS=60  ;;
  skill-creator) TIMEOUT=4800;  MAX_TURNS=250 ;;
  factory)       TIMEOUT=10800; MAX_TURNS=600 ;;
  *) echo "unknown arm: $ARM" >&2; exit 2 ;;
esac

# auth precheck BEFORE any run-dir creation (containment hides the interactive login;
# a headless credential is required: CLAUDE_CODE_OAUTH_TOKEN via `claude setup-token`, or ANTHROPIC_API_KEY)
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$BENCH/.auth-token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$BENCH/.auth-token")"; export CLAUDE_CODE_OAUTH_TOKEN
fi
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "run-arm.sh: no CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY (and no .auth-token file); aborting" >&2
  exit 2
fi

# Run dirs live OUTSIDE any git repo (scaffolders and git probes must not walk into the parent repo).
RUNS_ROOT="${BENCH_RUNS_ROOT:-${TMPDIR:-/tmp}/create-skill-autoresearch-bench}"
RUN="$RUNS_ROOT/$RUN_ID"
WORK="$RUN/work"
[ -e "$RUN" ] && { echo "run dir already exists: $RUN (run-ids are one-shot)" >&2; exit 2; }
mkdir -p "$WORK" "$RUN/home" "$HERE/../runs"
ln -sfn "$RUN" "$HERE/../runs/$RUN_ID"
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
	name = csa-benchmark
	email = csa-benchmark@localhost
[init]
	defaultBranch = main
EOF

# ---------- arm setup: skill injection into the fake HOME ----------
mkdir -p "$HOME/.claude/skills"
case "$ARM" in
  bare) ;;
  skill-creator)
    cp -R "$REPO/.agents/skills/skill-creator" "$HOME/.claude/skills/skill-creator"
    ;;
  factory)
    cp -R "$REPO/skills/create-skill-autoresearch" "$HOME/.claude/skills/create-skill-autoresearch"
    for s in autoresearch premortem handoff; do
      cp -R "$BENCH/companions/$s" "$HOME/.claude/skills/$s"
    done
    ;;
esac

# ---------- task setup: brief + materials into the workspace ----------
cp "$BENCH/task/brief.md" "$WORK/brief.md"
mkdir -p "$WORK/input" "$WORK/materials"
cp "$BENCH"/task/input/*.md "$WORK/input/"
cp "$BENCH"/task/materials/spec.md "$WORK/materials/spec.md"
# holdout cases and answer key are NEVER copied into the run

# ---------- render the prompt ----------
case "$ARM" in
  bare)
    EXTRA="Read brief.md, the gold standards in input/, and materials/spec.md, then write the skill in a single pass. Do not use any installed skills."
    ;;
  skill-creator)
    EXTRA="Use the skill-creator skill to build it. Read brief.md, the gold standards in input/, and materials/spec.md. Work fully non-interactively: brief.md answers every question you would ask the user; never wait for user input. Skip any packaging/upload steps — just produce the skill directory."
    ;;
  factory)
    EXTRA="Use the create-skill-autoresearch skill (the 5-phase factory) to build it. brief.md answers every Phase-1 interview question. You are running UNATTENDED in CI: there is no user, no one will ever reply, and pausing to ask or check in ends the run as a failure. Wherever the factory says to confirm, present, or check in with the user, adopt the brief's answer (or the factory's stated default) and continue immediately. Run ALL 5 phases to completion — including the Phase-4 autoresearch loop (budget max_iterations: 6) and the Phase-5 panel — before stopping. For any LLM-as-judge evaluation the factory builds, use the claude CLI itself (claude -p --model claude-haiku-4-5-20251001) rather than an external API."
    ;;
esac
PROMPT="You are in an empty workspace containing brief.md, input/, and materials/. Build the skill specified by brief.md. $EXTRA When you are done, the finished skill must exist at output/conventional-commits/SKILL.md."
printf '%s\n' "$PROMPT" > "$RUN/prompt.txt"

# ---------- run the agent ----------
START_TS="$(date +%s)"
set +e
# stdin MUST be /dev/null: an inherited empty stdin makes the CLI wait 3s, warn, and the agent
# can read the hiccup as a user interjection and pause mid-pipeline (bit factory-sonnet-2).
# env MUST be scrubbed (env -i + allowlist): a --dangerously-skip-permissions agent can printenv,
# so the caller's unrelated secrets (CI tokens, cloud creds) must never reach the session.
( cd "$WORK" && perl -e 'alarm shift @ARGV; exec @ARGV' -- "$TIMEOUT" env -i \
    PATH="$PATH" HOME="$HOME" TERM="${TERM:-dumb}" LANG="${LANG:-en_US.UTF-8}" SHELL="${SHELL:-/bin/bash}" \
    XDG_CONFIG_HOME="$XDG_CONFIG_HOME" XDG_CACHE_HOME="$XDG_CACHE_HOME" XDG_DATA_HOME="$XDG_DATA_HOME" \
    GIT_CONFIG_GLOBAL="$GIT_CONFIG_GLOBAL" GIT_CEILING_DIRECTORIES="$GIT_CEILING_DIRECTORIES" \
    CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    claude -p "$(cat "$RUN/prompt.txt")" \
    --model "$MODEL_ID" \
    --dangerously-skip-permissions \
    --max-turns "$MAX_TURNS" \
  ) <"/dev/null" >"$RUN/transcript.txt" 2>&1
EXIT_CODE=$?
set -e
END_TS="$(date +%s)"
WALL=$((END_TS - START_TS))

# ---------- env-failure detection (policy: rerun once, never score an env_failure) ----------
ENV_FAILURE=0
if [ "$EXIT_CODE" -ne 0 ]; then
  if grep -qiE 'ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|network error|socket hang up|503 Service|429 Too Many|fetch failed|API Error: Connection closed|overloaded_error' \
      "$RUN/transcript.txt" 2>/dev/null; then
    ENV_FAILURE=1
  fi
fi

# ---------- meta + results row ----------
printf '{ "run_id": "%s", "arm": "%s", "model": "%s", "model_id": "%s", "exit_code": %s, "wall_seconds": %s, "env_failure": %s }\n' \
  "$RUN_ID" "$ARM" "$MODEL_ALIAS" "$MODEL_ID" "$EXIT_CODE" "$WALL" "$ENV_FAILURE" > "$RUN/meta.json"
RESULTS="$BENCH/results/runs.csv"
mkdir -p "$BENCH/results"
[ -f "$RESULTS" ] || echo "run_id,arm,model,exit_code,wall_seconds,env_failure" > "$RESULTS"
echo "$RUN_ID,$ARM,$MODEL_ALIAS,$EXIT_CODE,$WALL,$ENV_FAILURE" >> "$RESULTS"

echo "# run $RUN_ID finished: exit=$EXIT_CODE wall=${WALL}s env_failure=$ENV_FAILURE" >&2
echo "# score: node $BENCH/scoring/score.mjs $RUN/work/output/conventional-commits" >&2
[ "$ENV_FAILURE" = "1" ] && exit 75
exit "$EXIT_CODE"
