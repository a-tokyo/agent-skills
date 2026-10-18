#!/usr/bin/env bash
# smoke-kill-guard.sh [model] — one cheap headless run proving the factory's kill guard is active when
# the benchmark loads the factory the way run-arm.sh's `factory` arm does (--plugin-dir, isolated HOME,
# scrubbed env, --dangerously-skip-permissions).
#
# The agent is asked to run a pkill on a pattern nothing matches, then an echo. PASS needs all of:
#   - the session jsonl holds the pkill tool call and a tool result carrying the guard's message;
#   - the echo ran (the session continued);
#   - the agent listed create-skill-autoresearch among its skills (the plugin's skill is discoverable).
# Then fidelity.py on the run dir must count the attempt as blocked, not executed.
#
#   bash arms/smoke-kill-guard.sh            # haiku; run dir under $BENCH_RUNS_ROOT (or $TMPDIR)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$BENCH/../.." && pwd)"
MODEL_ID="${1:-claude-haiku-5-5}"
FACTORY_DIR="${FACTORY_DIR:-$REPO/skills/create-skill-autoresearch}"
[ -f "$FACTORY_DIR/hooks/hooks.json" ] || { echo "no hooks/hooks.json in $FACTORY_DIR" >&2; exit 2; }

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$BENCH/.auth-token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$BENCH/.auth-token")"; export CLAUDE_CODE_OAUTH_TOKEN
fi
[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ] || { echo "no headless credential" >&2; exit 2; }

RUNS_ROOT="${BENCH_RUNS_ROOT:-${TMPDIR:-/tmp}/create-skill-autoresearch-bench}"
RUN="$RUNS_ROOT/smoke-kill-guard-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN/ws" "$RUN/home/.config" "$RUN/home/.cache" "$RUN/home/.local/share" "$RUN/plugins"
cp -R "$FACTORY_DIR" "$RUN/plugins/create-skill-autoresearch"
printf '[user]\n\tname = csa-benchmark\n\temail = csa-benchmark@localhost\n' >"$RUN/home/.gitconfig"

PATTERN="csa-smoke-no-such-process-$$"
PROMPT="Run this exact Bash command as its own tool call: pkill -f $PATTERN
Then run this Bash command: echo guard-smoke-continued
Then reply with three lines: the exact error or output the pkill call returned; the echo output; and the names of the skills available to you (from your skill list), comma-separated. Do not retry the pkill or work around it."

set +e
( cd "$RUN/ws" && perl -e 'alarm shift @ARGV; exec @ARGV' -- 600 env -i \
    PATH="$PATH" HOME="$RUN/home" TERM="${TERM:-dumb}" LANG="${LANG:-en_US.UTF-8}" SHELL="${SHELL:-/bin/bash}" \
    XDG_CONFIG_HOME="$RUN/home/.config" XDG_CACHE_HOME="$RUN/home/.cache" XDG_DATA_HOME="$RUN/home/.local/share" \
    GIT_CONFIG_GLOBAL="$RUN/home/.gitconfig" GIT_CEILING_DIRECTORIES="$RUNS_ROOT" \
    CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    DISABLE_AUTOUPDATER=1 \
    claude -p "$PROMPT" --model "$MODEL_ID" --dangerously-skip-permissions --max-turns 8 \
      --plugin-dir "$RUN/plugins/create-skill-autoresearch" \
  ) </dev/null >"$RUN/transcript.txt" 2>&1
EXIT_CODE=$?
set -e

JSONL="$(find "$RUN/home/.claude/projects" -name '*.jsonl' 2>/dev/null | head -1)"
check() { if grep -q -- "$2" "$3" 2>/dev/null; then echo "ok: $1"; else echo "FAIL: $1"; FAILED=1; fi; }
FAILED=0
echo "run dir: $RUN (claude exit $EXIT_CODE)"
check "pkill tool call issued" "pkill -f $PATTERN" "${JSONL:-/dev/null}"
check "guard message returned to the agent" "pattern kills hit unrelated processes" "${JSONL:-/dev/null}"
check "session continued (echo ran)" "guard-smoke-continued" "${JSONL:-/dev/null}"
check "factory skill discoverable" "create-skill-autoresearch" "$RUN/transcript.txt"
python3 -B "$BENCH/scoring/fidelity.py" "$RUN" | sed -n '1,2p'
[ "$FAILED" = 0 ] && echo "SMOKE PASS" || { echo "SMOKE FAIL"; exit 1; }
