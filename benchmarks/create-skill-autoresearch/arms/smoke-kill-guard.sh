#!/usr/bin/env bash
# smoke-kill-guard.sh [model] — one cheap headless run proving the factory's kill guard is active when
# the benchmark loads the factory the way run-arm.sh's `factory` arm does (--plugin-dir, isolated HOME,
# scrubbed env, --dangerously-skip-permissions).
#
# The agent is asked to run a pkill on a pattern nothing matches, then an echo. PASS needs all of:
#   - an assistant Bash tool_use ran the pkill, and the tool_result with its id is an error carrying
#     the guard's message (calls and results are paired by id, so the prompt text cannot match);
#   - the echo's tool_result is not an error and carries its output (the session continued);
#   - the agent listed create-skill-autoresearch among its skills (the plugin's skill is discoverable).
# Then fidelity.py on the run dir must count the attempt as blocked, not executed.
#
# A second run is an executor the way a build starts one: `claude -p --append-system-prompt …` through the
# build's shim (arms/claude-shim.sh) with no --plugin-dir, from the root of an unfinished fake build. PASS needs
# the same pkill blocked and echo run, and no work/experiments/finish-guard.jsonl in that build: the shim loaded
# the kill guard and set CSA_EXECUTOR, which keeps the finish guard from sending the executor back.
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
# The guard acts only inside a factory build: the first run starts in a fake one past Phase 1.
mkdir -p "$RUN/ws/work" "$RUN/home/.config" "$RUN/home/.cache" "$RUN/home/.local/share" "$RUN/plugins"
cp -R "$FACTORY_DIR" "$RUN/plugins/create-skill-autoresearch"
: >"$RUN/ws/work/manifest.yaml"
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

FAILED=0
echo "run dir: $RUN (claude exit $EXIT_CODE)"
# Pair each assistant Bash tool_use with the tool_result that answers it (by tool_use_id), so the prompt
# text in the session log can never satisfy a check: the pkill call must come back is_error with the
# guard's message, and the echo call must come back without error and carry its output.
cat >"$RUN/check.py" <<'PY'
import json, pathlib, sys
root, pkill = pathlib.Path(sys.argv[1]), sys.argv[2]
uses, results = {}, {}
for path in sorted(root.rglob("*.jsonl")) if root.is_dir() else []:
    for line in open(path, errors="replace"):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("type") not in ("assistant", "user"):
            continue
        for item in (entry.get("message") or {}).get("content") or []:
            if not isinstance(item, dict):
                continue
            if entry["type"] == "assistant" and item.get("type") == "tool_use" and item.get("name") == "Bash":
                uses[item.get("id")] = (item.get("input") or {}).get("command", "")
            elif entry["type"] == "user" and item.get("type") == "tool_result":
                content = item.get("content")
                text = content if isinstance(content, str) else " ".join(
                    c.get("text", "") for c in content or [] if isinstance(c, dict))
                results[item.get("tool_use_id")] = (bool(item.get("is_error")), text)
def answered(want):
    return [(cid, results.get(cid)) for cid, cmd in uses.items() if want in cmd]
failed = 0
def check(name, ok):
    global failed
    print(("ok: " if ok else "FAIL: ") + name)
    failed |= not ok
calls = answered(pkill)
check("pkill tool call issued (assistant tool_use)", bool(calls))
check("its tool_result is an error carrying the guard message",
      any(r and r[0] and "pattern kills hit unrelated processes" in r[1] for _, r in calls))
echo = answered("echo guard-smoke-continued")
check("session continued: the echo ran without error",
      any(r and not r[0] and "guard-smoke-continued" in r[1] for _, r in echo))
sys.exit(failed)
PY
python3 "$RUN/check.py" "$RUN/home/.claude/projects" "pkill -f $PATTERN" || FAILED=1
if grep -q -- "create-skill-autoresearch" "$RUN/transcript.txt" 2>/dev/null; then
  echo "ok: factory skill discoverable (listed in the agent's reply)"
else
  echo "FAIL: factory skill discoverable"; FAILED=1
fi
python3 -B "$BENCH/scoring/fidelity.py" "$RUN" | sed -n '1,2p'

# ---------- executor run: through the shim, no --plugin-dir, at an unfinished build's root ----------
EXEC="$RUN/executor"
mkdir -p "$EXEC/home/.config" "$EXEC/home/.cache" "$EXEC/home/.local/share" "$EXEC/bin" \
  "$EXEC/build/work/evaluation" "$EXEC/build/output/demo"
printf -- '---\nname: demo\ndescription: Demo.\n---\n' >"$EXEC/build/output/demo/SKILL.md"
cp "$RUN/home/.gitconfig" "$EXEC/home/.gitconfig"
# shellcheck source=claude-shim.sh
. "$HERE/claude-shim.sh"
write_claude_shim "$EXEC/bin/claude" "$(command -v claude)" "$EXEC/claude-calls.log" "$RUN/plugins/create-skill-autoresearch"
set +e
( cd "$EXEC/build" && perl -e 'alarm shift @ARGV; exec @ARGV' -- 600 env -i \
    PATH="$EXEC/bin:$PATH" HOME="$EXEC/home" TERM="${TERM:-dumb}" LANG="${LANG:-en_US.UTF-8}" SHELL="${SHELL:-/bin/bash}" \
    XDG_CONFIG_HOME="$EXEC/home/.config" XDG_CACHE_HOME="$EXEC/home/.cache" XDG_DATA_HOME="$EXEC/home/.local/share" \
    GIT_CONFIG_GLOBAL="$EXEC/home/.gitconfig" GIT_CEILING_DIRECTORIES="$RUNS_ROOT" \
    CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    DISABLE_AUTOUPDATER=1 \
    claude -p "${PROMPT%%Then reply*}Then reply with two lines: the exact error or output the pkill call returned; the echo output." \
      --model "$MODEL_ID" --dangerously-skip-permissions --max-turns 8 \
      --append-system-prompt "You are the executor of a skill under test. Follow the user's instructions exactly." \
  ) </dev/null >"$EXEC/transcript.txt" 2>&1
echo "executor run: $EXEC (claude exit $?)"
set -e
python3 "$RUN/check.py" "$EXEC/home/.claude/projects" "pkill -f $PATTERN" || FAILED=1
if [ -e "$EXEC/build/work/experiments/finish-guard.jsonl" ]; then
  echo "FAIL: the executor was not sent back by the finish guard"; FAILED=1
else
  echo "ok: the executor was not sent back by the finish guard (no finish-guard.jsonl)"
fi
[ "$FAILED" = 0 ] && echo "SMOKE PASS" || { echo "SMOKE FAIL"; exit 1; }
