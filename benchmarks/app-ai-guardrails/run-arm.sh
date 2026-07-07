#!/usr/bin/env bash
# run-arm.sh <arm> <model> <stack> <pm> <run-id>
#
#   arm     bare | with-skill
#   model   haiku | sonnet | opus   (mapped to exact model ids below)
#   stack   next | nest | django | go | rust | springboot
#   pm      npm | pnpm | yarn | bun | uv | go | cargo | gradle   (recorded; JS stacks may vary the prompt's PM wording)
#   run-id  unique id for this run; everything lands in runs/<run-id>/
#
# Launches ONE fresh `claude -p` benchmark run in runs/<run-id>/work/ under full containment (D6):
# isolated HOME + GIT_CONFIG_GLOBAL + XDG dirs so a --dangerously-skip-permissions agent can never read
# or write the maintainer's real config, credentials, or caches. Only package-manager caches are shared,
# via explicit env vars pointed at benchmarks/cache/<ecosystem>/ (speed without leakage).
#
# Scoring is NOT done here -- run evaluate.sh on runs/<run-id>/work afterwards. This split keeps the
# scorer deterministic and lets a failed/env-flaky agent run be classified (env_failure) without a score.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARM="${1:?usage: run-arm.sh <bare|with-skill> <model> <stack> <pm> <run-id>}"
MODEL_ALIAS="${2:?missing model}"
STACK="${3:?missing stack}"
PM="${4:?missing pm}"
RUN_ID="${5:?missing run-id}"

case "$MODEL_ALIAS" in
  haiku)  MODEL_ID="claude-haiku-4-5-20251001" ;;
  sonnet) MODEL_ID="claude-sonnet-5" ;;
  opus)   MODEL_ID="claude-opus-4-8" ;;
  *) echo "unknown model alias: $MODEL_ALIAS" >&2; exit 2 ;;
esac
case "$ARM" in bare|with-skill) ;; *) echo "unknown arm: $ARM" >&2; exit 2 ;; esac

# auth precheck BEFORE any run-dir creation, so a misconfigured call never leaves a stale
# one-shot run dir behind (bit us on 2026-07-03). Full guidance repeated at the auth section.
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$HERE/.auth-token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$HERE/.auth-token")"; export CLAUDE_CODE_OAUTH_TOKEN
fi
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "run-arm.sh: no CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY (and no .auth-token file); aborting before creating the run dir" >&2
  exit 2
fi

# Run dirs MUST NOT be nested inside any git repo: scaffolders detect an ancestor .git and skip
# `git init` (bit us on 2026-07-03: CNA silently skipped git init because benchmarks/ lives inside
# the workspace repo, so git probes leaked to the parent). Physical home is under /private/tmp;
# runs/<id> stays as a symlink for ergonomics. Override with BENCH_RUNS_ROOT.
RUNS_ROOT="${BENCH_RUNS_ROOT:-/private/tmp/app-ai-guardrails-bench}"
RUN="$RUNS_ROOT/$RUN_ID"
WORK="$RUN/work"
[ -e "$RUN" ] && { echo "run dir already exists: $RUN (run-ids are one-shot)" >&2; exit 2; }
[ -L "$HERE/runs/$RUN_ID" ] && { echo "run symlink already exists: runs/$RUN_ID (run-ids are one-shot)" >&2; exit 2; }
mkdir -p "$WORK" "$RUN/home" "$HERE/runs"
ln -s "$RUN" "$HERE/runs/$RUN_ID"
# belt-and-braces: stop git (and git-aware tools) from ever walking above the run dir
export GIT_CEILING_DIRECTORIES="$RUNS_ROOT"

# ---------- containment (D6): isolated HOME/GIT_CONFIG_GLOBAL/XDG ----------
export HOME="$RUN/home"
export XDG_CONFIG_HOME="$RUN/home/.config"
export XDG_CACHE_HOME="$RUN/home/.cache"
export XDG_DATA_HOME="$RUN/home/.local/share"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME"
export GIT_CONFIG_GLOBAL="$RUN/home/.gitconfig"
cat > "$GIT_CONFIG_GLOBAL" <<'EOF'
[user]
	name = guardrails-benchmark
	email = guardrails-benchmark@localhost
[init]
	defaultBranch = main
EOF

# ---------- shared toolchain env (caches + tool PATHs) ----------
# Single source of truth with evaluate.sh — the scorer must see the same toolchain the agent had
# (see toolchain-env.sh header for the capped-50 incident this prevents).
BENCH_HERE="$HERE" source "$HERE/toolchain-env.sh"
# ---------- headless auth under the fake HOME ----------
# The claude CLI's interactive login lives in the real HOME/keychain, which containment hides.
# Benchmark runs therefore REQUIRE a headless credential in env (verified on this machine 2026-07-03:
# fake HOME without one -> "Not logged in"):
#   CLAUDE_CODE_OAUTH_TOKEN  minted once via `claude setup-token` (recommended; revocable), or
#   ANTHROPIC_API_KEY        a Console API key (bills API credits instead).
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "run-arm.sh: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set;" >&2
  echo "containment hides the interactive login, so this run would fail 'Not logged in'." >&2
  echo "mint a token with: claude setup-token   (then export CLAUDE_CODE_OAUTH_TOKEN)" >&2
  exit 2
fi

# ---------- arm setup: skill presence ----------
# The skill is injected as a USER-level skill in the run's ISOLATED HOME ($HOME is already the fake
# home here). Two prior placements both failed (2026-07-03):
#   - inside work/.claude/skills  -> contaminated the scored git tree (agents committed harness files)
#   - at the run root (parent of work/) -> NEVER DISCOVERED: Claude Code does not walk ancestors of
#     cwd for .claude/skills (verified: with-skill run scored bare-band 39, transcript had zero
#     mentions of the skill)
# User-level ~/.claude/skills is always loaded, and the fake HOME makes it per-run and containment-safe.
mkdir -p "$HOME/.claude/skills"
if [ "$ARM" = "with-skill" ]; then
  SKILL_SRC="$HERE/../../skills/app-ai-guardrails"
  [ -d "$SKILL_SRC" ] && [ -n "$(ls -A "$SKILL_SRC" 2>/dev/null)" ] || {
    echo "with-skill arm requires the shipped skill at $SKILL_SRC" >&2; exit 2; }
  cp -R "$SKILL_SRC" "$HOME/.claude/skills/app-ai-guardrails"
fi

# ---------- render the prompt ----------
STACK_LABEL="$(STACK="$STACK" STACKS_JSON="$HERE/task/stacks.json" node -e '
  const s = require(process.env.STACKS_JSON)[process.env.STACK];
  if (!s) { console.error("unknown stack: " + process.env.STACK); process.exit(2); }
  process.stdout.write(s.label);
')"
# prompt text = everything below the `---` separator of the template file, {{STACK}} substituted.
PROMPT="$(awk 'flag{print} /^---$/{flag=1}' "$HERE/task/prompt-template.md" | sed -e 's/^[[:space:]]*//' | tr '\n' ' ' | sed -e 's/  */ /g' -e 's/^ //' -e 's/ $//')"
PROMPT="${PROMPT//\{\{STACK\}\}/$STACK_LABEL}"
# JS stacks: name the requested package manager neutrally (npm is default; naming pnpm/bun is a task
# parameter, not a probe leak).
if { [ "$STACK" = "next" ] || [ "$STACK" = "nest" ]; } && [ "$PM" != "npm" ]; then
  PROMPT="$PROMPT Use $PM as the package manager."
fi
printf '%s\n' "$PROMPT" > "$RUN/prompt.txt"

# ---------- record tool versions ----------
versions_json() {
  node -e '
    const { execSync } = require("child_process");
    const v = (cmd) => { try { return execSync(cmd, {stdio: ["ignore","pipe","ignore"]}).toString().trim().split("\n")[0]; } catch { return null; } };
    process.stdout.write(JSON.stringify({
      node: v("node -v"), npm: v("npm -v"), pnpm: v("pnpm -v"), bun: v("bun -v"),
      uv: v("uv --version"), go: v("go version"), cargo: v("cargo --version"),
      claude: v("claude --version"), create_next_app: v("npx --yes create-next-app@latest --version"),
    }));
  '
}

# ---------- run the agent ----------
START_TS="$(date +%s)"
set +e
# portable timeout: macOS ships no coreutils `timeout`; perl's alarm+exec is always available.
( cd "$WORK" && perl -e 'alarm shift @ARGV; exec @ARGV' -- 2400 claude -p "$(cat "$RUN/prompt.txt")" \
    --model "$MODEL_ID" \
    --dangerously-skip-permissions \
    --max-turns 150 \
  ) 2>&1 | tee "$RUN/transcript.txt"
EXIT_CODE=${PIPESTATUS[0]}
set -e
END_TS="$(date +%s)"
WALL=$((END_TS - START_TS))

# ---------- env-failure detection (policy: rerun once, never score an env_failure) ----------
ENV_FAILURE=0
if [ "$EXIT_CODE" -ne 0 ]; then
  if grep -qiE 'ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|registry error|network error|socket hang up|503 Service|429 Too Many|fetch failed|unable to access .*github|API Error: Connection closed|Connection closed mid-response|overloaded_error' \
      "$RUN/transcript.txt" 2>/dev/null; then
    ENV_FAILURE=1
  fi
fi

# ---------- meta.json + results row ----------
EXIT_CODE="$EXIT_CODE" WALL="$WALL" ARM="$ARM" MODEL_ALIAS="$MODEL_ALIAS" MODEL_ID="$MODEL_ID" \
STACK="$STACK" PM="$PM" RUN_ID="$RUN_ID" ENV_FAILURE="$ENV_FAILURE" VERSIONS="$(versions_json)" \
node -e '
  const e = process.env;
  const meta = {
    run_id: e.RUN_ID, arm: e.ARM, model: e.MODEL_ALIAS, model_id: e.MODEL_ID,
    stack: e.STACK, pm: e.PM,
    exit_code: Number(e.EXIT_CODE), wall_seconds: Number(e.WALL),
    env_failure: e.ENV_FAILURE === "1",
    started_at: new Date(Date.now() - Number(e.WALL) * 1000).toISOString(),
    versions: JSON.parse(e.VERSIONS),
  };
  require("fs").writeFileSync(process.argv[1], JSON.stringify(meta, null, 2) + "\n");
' "$RUN/meta.json"

RESULTS="$HERE/results/runs.csv"
mkdir -p "$HERE/results"
[ -f "$RESULTS" ] || echo "run_id,arm,model,stack,pm,exit_code,wall_seconds,env_failure" > "$RESULTS"
echo "$RUN_ID,$ARM,$MODEL_ALIAS,$STACK,$PM,$EXIT_CODE,$WALL,$ENV_FAILURE" >> "$RESULTS"

echo "# run $RUN_ID finished: exit=$EXIT_CODE wall=${WALL}s env_failure=$ENV_FAILURE" >&2
echo "# score it with: ./evaluate.sh runs/$RUN_ID/work" >&2
[ "$ENV_FAILURE" = "1" ] && exit 75  # EX_TEMPFAIL: caller should rerun once, never score
exit "$EXIT_CODE"
