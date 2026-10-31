#!/usr/bin/env bash
# run-arm.sh <arm> <model> <run-id> [task]
#
#   arm     bare | skill-creator | factory | factory-v021
#   model   haiku | sonnet | opus   (builder model; the scorer's executor model is fixed separately)
#   run-id  unique id; everything lands in runs/<run-id>/
#   task    task (conventional-commits, default) | task-2 (purchase-order-builder) | task-3 (student-aid-index)
#
#   FACTORY_DIR / COMPANIONS_DIR override where the factory arms load the skill and its companions;
#   factory-v021 requires both (the pinned v0.2.1 archive, agent-skills commit 99652ee).
#
#   HOST=cursor runs the builder with `cursor-agent -p --force --trust --output-format stream-json`
#   instead of `claude -p` (model: CURSOR_MODEL, default per alias, Sonnet -> claude-sonnet-5-5-high).
#   The skills go in the workspace's .agents/skills/ (Cursor's project skills dir) with the kill guard
#   as a project hook in .cursor/hooks.json, and PATH holds no `claude` (checked before the run, recorded
#   in meta.json). cursor-agent keeps its login in the macOS login Keychain, so on macOS HOME is isolated
#   too, with one symlink (Library/Keychains) carrying the login: the maintainer's ~/.cursor, ~/.claude and
#   ~/.agents skills, rules, plugins and hooks are not visible. Cursor's own bundled skills and any
#   team-managed plugins still load (they come with the account). Elsewhere HOME stays the maintainer's;
#   meta.json records home_isolated.
#
# Launches ONE fresh `claude -p` build run in runs/<run-id>/ws/ under full containment:
# isolated HOME + GIT_CONFIG_GLOBAL + XDG dirs so a --dangerously-skip-permissions agent can never
# read or write the maintainer's real config, credentials, or caches. Pattern proven in the
# app-ai-guardrails benchmark harness (isolated-HOME skill injection: user-level ~/.claude/skills
# in the fake HOME is the only placement Claude Code reliably discovers). On macOS that HOME gets an empty,
# unlocked throwaway default keychain (throwaway-keychain.sh), so storing a credential raises no dialog.
#
# Scoring is NOT done here — run the scorer afterwards:
#   node ../scoring/score.mjs runs/<run-id>/ws/output/conventional-commits
# (the build dir is ws/, not work/, because the factory creates its own work/ inside it; run dirs
# from before the rename keep work/, and run-and-score.sh falls back to it.)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$BENCH/../.." && pwd)"

ARM="${1:?usage: run-arm.sh <bare|skill-creator|factory|factory-v021> <model> <run-id> [task]}"
MODEL_ALIAS="${2:?missing model}"
RUN_ID="${3:?missing run-id}"
TASK_NAME="${4:-task}"
case "$TASK_NAME" in
  task)   SKILL_OUT="conventional-commits" ;;
  task-2) SKILL_OUT="purchase-order-builder" ;;
  task-3) SKILL_OUT="student-aid-index" ;;
  *) echo "unknown task: $TASK_NAME" >&2; exit 2 ;;
esac
TASK_DIR="$BENCH/$TASK_NAME"

case "$MODEL_ALIAS" in
  haiku)  MODEL_ID="claude-haiku-5-5" ;;
  sonnet) MODEL_ID="claude-sonnet-5-5" ;;
  opus)   MODEL_ID="claude-opus-5-5" ;;
  *) echo "unknown model alias: $MODEL_ALIAS" >&2; exit 2 ;;
esac
HOST="${HOST:-claude}"
case "$HOST" in
  claude) ;;
  cursor)
    case "$MODEL_ALIAS" in
      haiku)  DEFAULT_CURSOR_MODEL="claude-haiku-5-5-high" ;;
      sonnet) DEFAULT_CURSOR_MODEL="claude-sonnet-5-5-high" ;;
      opus)   DEFAULT_CURSOR_MODEL="claude-opus-5-5-high" ;;
    esac
    CURSOR_MODEL="${CURSOR_MODEL:-$DEFAULT_CURSOR_MODEL}"
    CURSOR_BIN="$(command -v cursor-agent)" || { echo "HOST=cursor needs cursor-agent on PATH" >&2; exit 2; }
    CURSOR_BIN="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$CURSOR_BIN")"
    # PATH for the build: every PATH dir that holds no `claude`, plus cursor-agent's own install dir.
    NO_CLAUDE_PATH="$(dirname "$CURSOR_BIN")"
    IFS=: read -r -a PATH_DIRS <<<"$PATH"
    for d in "${PATH_DIRS[@]}"; do
      [ -n "$d" ] && [ ! -e "$d/claude" ] && NO_CLAUDE_PATH="$NO_CLAUDE_PATH:$d"
    done
    if env -i PATH="$NO_CLAUDE_PATH" /bin/sh -c 'command -v claude' >/dev/null 2>&1; then
      echo "HOST=cursor: claude is still on the build PATH; aborting" >&2; exit 2
    fi
    ;;
  *) echo "unknown HOST: $HOST (claude | cursor)" >&2; exit 2 ;;
esac
case "$ARM" in
  bare)          TIMEOUT=1200;  MAX_TURNS=60  ;;
  skill-creator) TIMEOUT=4800;  MAX_TURNS=250 ;;
  factory|factory-v021) TIMEOUT=10800; MAX_TURNS=600 ;;
  *) echo "unknown arm: $ARM" >&2; exit 2 ;;
esac
if [ "$ARM" = "factory-v021" ] && { [ -z "${FACTORY_DIR:-}" ] || [ -z "${COMPANIONS_DIR:-}" ]; }; then
  echo "factory-v021 needs FACTORY_DIR and COMPANIONS_DIR (the pinned v0.2.1 archive)" >&2; exit 2
fi
FACTORY_DIR="${FACTORY_DIR:-$REPO/skills/create-skill-autoresearch}"
COMPANIONS_DIR="${COMPANIONS_DIR:-$BENCH/companions}"

# auth precheck BEFORE any run-dir creation (containment hides the interactive login;
# a headless credential is required: CLAUDE_CODE_OAUTH_TOKEN via `claude setup-token`, or ANTHROPIC_API_KEY)
if [ "$HOST" = "claude" ]; then
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$BENCH/.auth-token" ]; then
    CLAUDE_CODE_OAUTH_TOKEN="$(cat "$BENCH/.auth-token")"; export CLAUDE_CODE_OAUTH_TOKEN
  fi
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "run-arm.sh: no CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY (and no .auth-token file); aborting" >&2
    exit 2
  fi
elif ! "$CURSOR_BIN" status 2>&1 | grep -q "Logged in"; then
  echo "run-arm.sh: cursor-agent is not logged in (cursor-agent login); aborting" >&2
  exit 2
fi

# Run dirs live OUTSIDE any git repo (scaffolders and git probes must not walk into the parent repo).
RUNS_ROOT="${BENCH_RUNS_ROOT:-${TMPDIR:-/tmp}/create-skill-autoresearch-bench}"
RUN="$RUNS_ROOT/$RUN_ID"
WORK="$RUN/ws"
[ -e "$RUN" ] && { echo "run dir already exists: $RUN (run-ids are one-shot)" >&2; exit 2; }
mkdir -p "$WORK" "$RUN/home" "$HERE/../runs"
ln -sfn "$RUN" "$HERE/../runs/$RUN_ID"
export GIT_CEILING_DIRECTORIES="$RUNS_ROOT"

# ---------- containment: isolated HOME/GIT_CONFIG_GLOBAL/XDG ----------
# (HOST=cursor off macOS keeps the real HOME and XDG dirs for cursor-agent's login; git config is still isolated)
REAL_HOME="$HOME"
HOME_ISOLATED=true
if [ "$HOST" = "cursor" ] && { [ "$(uname -s)" != "Darwin" ] || [ ! -d "$REAL_HOME/Library/Keychains" ]; }; then
  HOME_ISOLATED=false
fi
CURSOR_XDG=()
if [ "$HOME_ISOLATED" = true ]; then
  export HOME="$RUN/home"
  export XDG_CONFIG_HOME="$RUN/home/.config"
  export XDG_CACHE_HOME="$RUN/home/.cache"
  export XDG_DATA_HOME="$RUN/home/.local/share"
  mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME"
  if [ "$HOST" = "cursor" ]; then
    mkdir -p "$HOME/Library"
    ln -s "$REAL_HOME/Library/Keychains" "$HOME/Library/Keychains"
    CURSOR_XDG=(XDG_CONFIG_HOME="$XDG_CONFIG_HOME" XDG_CACHE_HOME="$XDG_CACHE_HOME" XDG_DATA_HOME="$XDG_DATA_HOME")
    if ! "$CURSOR_BIN" status 2>&1 | grep -q "Logged in"; then
      echo "run-arm.sh: cursor-agent is not logged in under the isolated HOME; aborting" >&2
      exit 2
    fi
  else
    # macOS: an empty throwaway default keychain, so nothing raises a "Keychain Not Found" dialog
    # shellcheck source=throwaway-keychain.sh
    . "$HERE/throwaway-keychain.sh"
    make_throwaway_keychain "$HOME" || exit 2
  fi
fi
export GIT_CONFIG_GLOBAL="$RUN/home/.gitconfig"
cat > "$GIT_CONFIG_GLOBAL" <<'EOF'
[user]
	name = csa-benchmark
	email = csa-benchmark@localhost
[init]
	defaultBranch = main
EOF

# ---------- arm setup: skill injection into the fake HOME ----------
PLUGIN_ARGS=()
if [ "$HOST" = "cursor" ]; then
  # Cursor reads project skills from the workspace's .agents/skills/; the kill guard runs as a
  # project hook (beforeShellExecution), the npx-install path hosts.md documents.
  SKILLS_DIR="$WORK/.agents/skills"
  mkdir -p "$SKILLS_DIR" "$WORK/.cursor"
  case "$ARM" in
    skill-creator) cp -R "$REPO/.agents/skills/skill-creator" "$SKILLS_DIR/skill-creator" ;;
    factory|factory-v021)
      cp -R "$FACTORY_DIR" "$SKILLS_DIR/create-skill-autoresearch"
      for s in "$COMPANIONS_DIR"/*/; do
        [ -f "$s/SKILL.md" ] && cp -R "$s" "$SKILLS_DIR/$(basename "$s")"
      done
      GUARD_DIR="$SKILLS_DIR/create-skill-autoresearch/hooks"
      GUARD_CMD=""
      if [ -f "$GUARD_DIR/kill-guard" ]; then
        GUARD_CMD="sh $GUARD_DIR/kill-guard"
      elif [ -f "$GUARD_DIR/kill_guard.py" ]; then
        GUARD_CMD="python3 $GUARD_DIR/kill_guard.py"
      fi
      # The finish guard (v0.3.0+) rides along as a `stop` hook, as hosts.md documents; cursor-agent -p
      # 2026.10.18 runs no stop hook, so on this CLI it is installed but never fires.
      STOP_HOOK=""
      if [ -f "$GUARD_DIR/finish-guard" ]; then
        STOP_HOOK=", \"stop\": [ { \"command\": \"sh $GUARD_DIR/finish-guard\", \"timeout\": 30, \"loop_limit\": 1 } ]"
      fi
      if [ -n "$GUARD_CMD" ]; then
        printf '{ "version": 1, "hooks": { "beforeShellExecution": [ { "command": "%s", "timeout": 10 } ]%s } }\n' \
          "$GUARD_CMD" "$STOP_HOOK" > "$WORK/.cursor/hooks.json"
      fi
      ;;
  esac
else
mkdir -p "$HOME/.claude/skills"
case "$ARM" in
  bare) ;;
  skill-creator)
    cp -R "$REPO/.agents/skills/skill-creator" "$HOME/.claude/skills/skill-creator"
    ;;
  factory|factory-v021)
    # A factory that ships hooks/hooks.json (v0.3.0+: the kill guard) loads as a plugin so its hooks
    # are active; Claude Code loads a directory with SKILL.md at its root as a one-skill plugin.
    # The pinned v0.2.1 has no hooks and keeps the user-level skill placement.
    if [ -f "$FACTORY_DIR/hooks/hooks.json" ]; then
      mkdir -p "$RUN/plugins"
      cp -R "$FACTORY_DIR" "$RUN/plugins/create-skill-autoresearch"
      PLUGIN_ARGS=(--plugin-dir "$RUN/plugins/create-skill-autoresearch")
    else
      cp -R "$FACTORY_DIR" "$HOME/.claude/skills/create-skill-autoresearch"
    fi
    for s in "$COMPANIONS_DIR"/*/; do
      [ -f "$s/SKILL.md" ] && cp -R "$s" "$HOME/.claude/skills/$(basename "$s")"
    done
    ;;
esac
fi

# ---------- task setup: brief + materials into the workspace ----------
cp "$TASK_DIR/brief.md" "$WORK/brief.md"
cp -R "$TASK_DIR/input" "$WORK/input"
cp -R "$TASK_DIR/materials" "$WORK/materials"
# holdout cases and answer key are NEVER copied into the run

# ---------- render the prompt ----------
case "$ARM" in
  bare)
    EXTRA="Read brief.md, the gold standards in input/, and materials/, then write the skill in a single pass. Do not use any installed skills."
    ;;
  skill-creator)
    EXTRA="Use the skill-creator skill to build it. Read brief.md, the gold standards in input/, and materials/. Work fully non-interactively: brief.md answers every question you would ask the user; never wait for user input. Skip any packaging/upload steps — just produce the skill directory."
    ;;
  factory|factory-v021)
    EXTRA="Use the create-skill-autoresearch skill (the 5-phase factory) to build it. brief.md answers every Phase-1 interview question. You are running UNATTENDED in CI: there is no user, no one will ever reply, and pausing to ask or check in ends the run as a failure. Wherever the factory says to confirm, present, or check in with the user, adopt the brief's answer (or the factory's stated default) and continue immediately. Run ALL 5 phases to completion — including the Phase-4 autoresearch loop (budget max_iterations: 6) and the Phase-5 panel — before stopping. For any LLM-as-judge evaluation the factory builds, use the claude CLI itself (claude -p --model claude-haiku-5-5) rather than an external API."
    [ "$HOST" = "cursor" ] && EXTRA="${EXTRA%For any LLM-as-judge*}You are running in Cursor's CLI and the claude CLI is not installed. For any LLM call the factory builds (an executor or an LLM-as-judge), use the host's headless CLI as the factory describes rather than an external API."
    ;;
esac
PROMPT="You are in an empty workspace containing brief.md, input/, and materials/. Build the skill specified by brief.md. $EXTRA When you are done, the finished skill must exist at output/$SKILL_OUT/SKILL.md."
printf '%s\n' "$PROMPT" > "$RUN/prompt.txt"

# ---------- run the agent ----------
START_TS="$(date +%s)"
set +e
# stdin MUST be /dev/null: an inherited empty stdin makes the CLI wait 3s, warn, and the agent
# can read the hiccup as a user interjection and pause mid-pipeline (bit factory-sonnet-2).
# env MUST be scrubbed (env -i + allowlist): a --dangerously-skip-permissions agent can printenv,
# so the caller's unrelated secrets (CI tokens, cloud creds) must never reach the session.
if [ "$HOST" = "cursor" ]; then
( cd "$WORK" && perl -e 'alarm shift @ARGV; exec @ARGV' -- "$TIMEOUT" env -i \
    PATH="$NO_CLAUDE_PATH" HOME="$HOME" ${CURSOR_XDG[@]+"${CURSOR_XDG[@]}"} \
    USER="${USER:-}" LOGNAME="${LOGNAME:-}" TMPDIR="${TMPDIR:-/tmp}" \
    TERM="${TERM:-dumb}" LANG="${LANG:-en_US.UTF-8}" SHELL="${SHELL:-/bin/bash}" \
    GIT_CONFIG_GLOBAL="$GIT_CONFIG_GLOBAL" GIT_CEILING_DIRECTORIES="$GIT_CEILING_DIRECTORIES" \
    "$CURSOR_BIN" -p "$(cat "$RUN/prompt.txt")" \
    --model "$CURSOR_MODEL" --force --trust --output-format stream-json \
  ) <"/dev/null" >"$RUN/transcript.txt" 2>&1
EXIT_CODE=$?
else
# The build's `claude` is a shim (arms/claude-shim.sh): it logs each call, drops --no-session-persistence so
# cost metering sees every executor, and on a hooked factory arm loads the factory plugin (its kill guard) into
# any `-p` call that lacks --plugin-dir.
REAL_CLAUDE="$(command -v claude)"
GUARD_PLUGIN=""
[ -n "${PLUGIN_ARGS[*]+x}" ] && GUARD_PLUGIN="${PLUGIN_ARGS[1]}"
mkdir -p "$RUN/bin"
# shellcheck source=claude-shim.sh
. "$HERE/claude-shim.sh"
write_claude_shim "$RUN/bin/claude" "$REAL_CLAUDE" "$RUN/claude-calls.log" "$GUARD_PLUGIN"
( cd "$WORK" && perl -e 'alarm shift @ARGV; exec @ARGV' -- "$TIMEOUT" env -i \
    PATH="$RUN/bin:$PATH" HOME="$HOME" TERM="${TERM:-dumb}" LANG="${LANG:-en_US.UTF-8}" SHELL="${SHELL:-/bin/bash}" \
    XDG_CONFIG_HOME="$XDG_CONFIG_HOME" XDG_CACHE_HOME="$XDG_CACHE_HOME" XDG_DATA_HOME="$XDG_DATA_HOME" \
    GIT_CONFIG_GLOBAL="$GIT_CONFIG_GLOBAL" GIT_CEILING_DIRECTORIES="$GIT_CEILING_DIRECTORIES" \
    CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    DISABLE_AUTOUPDATER=1 \
    claude -p "$(cat "$RUN/prompt.txt")" \
    --model "$MODEL_ID" \
    --dangerously-skip-permissions \
    --max-turns "$MAX_TURNS" \
    ${PLUGIN_ARGS[@]+"${PLUGIN_ARGS[@]}"} \
  ) <"/dev/null" >"$RUN/transcript.txt" 2>&1
EXIT_CODE=$?
fi
set -e
END_TS="$(date +%s)"
WALL=$((END_TS - START_TS))

# ---------- env-failure detection (policy: rerun once, never score an env_failure) ----------
ENV_FAILURE=0
# 143 = SIGTERM from outside the run (a Claude Code self-update terminates every live session).
[ "$EXIT_CODE" -eq 143 ] && ENV_FAILURE=1
if [ "$EXIT_CODE" -ne 0 ]; then
  if grep -qiE 'ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|network error|socket hang up|503 Service|429 Too Many|fetch failed|API Error: Connection closed|overloaded_error' \
      "$RUN/transcript.txt" 2>/dev/null; then
    ENV_FAILURE=1
  fi
fi

# ---------- token usage (offline, from the fake HOME's transcripts; subagents included) ----------
# A failed sum leaves tokens null and the CSV cells empty rather than failing a finished build.
if [ "$HOST" = "cursor" ]; then
  # cursor-agent's stream-json ends with a result event carrying the session's usage (top-level session
  # only; subagent usage is not broken out there).
  TOKENS="$(python3 - "$RUN/transcript.txt" <<'PY' 2>/dev/null
import json, sys
usage = None
for line in open(sys.argv[1], errors="replace"):
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(event, dict) and event.get("type") == "result" and isinstance(event.get("usage"), dict):
        usage = event["usage"]
if usage:
    total = sum(int(usage.get(k) or 0) for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"))
    print(json.dumps({"input_total": total, "output_tokens": int(usage.get("outputTokens") or 0),
                      "source": "cursor-result"}, separators=(",", ":")))  # the token_field sed expects no spaces
PY
)" || TOKENS=""
  MODEL_ID="cursor:$CURSOR_MODEL"
else
TOKENS="$(node "$HERE/sum-tokens.mjs" "$HOME/.claude/projects" 2>/dev/null)" || TOKENS=""
fi
[ -n "$TOKENS" ] || TOKENS="null"
token_field() { printf '%s' "$TOKENS" | sed -n "s/.*\"$1\":\([0-9]*\).*/\1/p"; }
IN_TOKENS="$(token_field input_total)"
OUT_TOKENS="$(token_field output_tokens)"

# ---------- meta + results row ----------
CLAUDE_ON_PATH=true
[ "$HOST" = "cursor" ] && CLAUDE_ON_PATH=false
printf '{ "run_id": "%s", "task": "%s", "arm": "%s", "model": "%s", "model_id": "%s", "host": "%s", "claude_on_path": %s, "home_isolated": %s, "exit_code": %s, "wall_seconds": %s, "env_failure": %s, "tokens": %s }\n' \
  "$RUN_ID" "$TASK_NAME" "$ARM" "$MODEL_ALIAS" "$MODEL_ID" "$HOST" "$CLAUDE_ON_PATH" "$HOME_ISOLATED" "$EXIT_CODE" "$WALL" "$ENV_FAILURE" "$TOKENS" > "$RUN/meta.json"
RESULTS="$BENCH/results/runs.csv"
mkdir -p "$BENCH/results"
[ -f "$RESULTS" ] || echo "run_id,arm,model,exit_code,wall_seconds,env_failure,task,input_tokens,output_tokens" > "$RESULTS"
echo "$RUN_ID,$ARM,$MODEL_ALIAS,$EXIT_CODE,$WALL,$ENV_FAILURE,$TASK_NAME,$IN_TOKENS,$OUT_TOKENS" >> "$RESULTS"

echo "# run $RUN_ID finished: exit=$EXIT_CODE wall=${WALL}s env_failure=$ENV_FAILURE in_tokens=${IN_TOKENS:-?} out_tokens=${OUT_TOKENS:-?}" >&2
SCORER="score.mjs"; [ "$TASK_NAME" = "task-2" ] && SCORER="score-task2.mjs"; [ "$TASK_NAME" = "task-3" ] && SCORER="score-task3.mjs"
echo "# score: node $BENCH/scoring/$SCORER $RUN/ws/output/$SKILL_OUT" >&2
[ "$ENV_FAILURE" = "1" ] && exit 75
exit "$EXIT_CODE"
