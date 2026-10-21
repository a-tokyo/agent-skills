# Hosts

The factory is an Agent Skill: any agent that loads `SKILL.md` runs it. This file names what each host
calls the pieces the pipeline uses. Wherever the factory says **the host's headless CLI**, use the
Headless row for your host. Facts from each host's official docs, read on 2026-10-19
([Claude Code](https://code.claude.com/docs/en/skills), [Cursor](https://cursor.com/docs/skills),
[Codex](https://developers.openai.com/codex/skills), [OpenCode](https://opencode.ai/docs/skills/)); "verified"
marks what the factory's own tests or benchmark ran.

## Contents
- Skills and install
- Headless runs, judges and executors
- Subagents
- Kill guard
- Finish guard
- Operating systems
- What needs Claude Code

## Skills and install

`npx skills add <source> --skill <name> -a <claude-code|cursor|codex|opencode>` (`-g` for user-level).

| Host | Project skills | User skills | Manual-only (user-invoked) |
|---|---|---|---|
| Claude Code | `.claude/skills/` | `~/.claude/skills/` | `disable-model-invocation: true` |
| Cursor | `.cursor/skills/`, `.agents/skills/` (reads `.claude/skills/`, `.codex/skills/` too) | `~/.cursor/skills/`, `~/.agents/skills/` | `disable-model-invocation: true`; `name` must match the folder |
| Codex | `.agents/skills/` | `~/.agents/skills/` | `policy.allow_implicit_invocation: false` in `agents/openai.yaml` |
| OpenCode | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` | `~/.config/opencode/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | `metadata: {opencode/autoinvoke: "false"}` |

A skill the factory builds for several hosts keeps `disable-model-invocation` and adds the other hosts'
switch only when the user asks for manual-only there.

## Headless runs, judges and executors

| Host | Command | Model | Auto-approve | JSON |
|---|---|---|---|---|
| Claude Code | `claude -p "<prompt>"` | `--model <id>` | `--allowedTools …`, `--dangerously-skip-permissions` | `--output-format json\|stream-json` |
| Cursor | `cursor-agent -p "<prompt>"` | `--model <id>` (`--list-models`) | `--force --trust` | `--output-format json\|stream-json` |
| Codex | `codex exec --skip-git-repo-check "<prompt>"` | `-m <id>` | `--sandbox workspace-write` | `--json` |
| OpenCode | `opencode run "<prompt>"` | `-m <provider/model>` | `--auto` | `--format json` |

The same command serves every headless role: the 3.4 executor and judge, the manual-protocol agentic
executor and `llm` grader (benchmark-standard.md), and a fresh-session subagent. Pass the skill as context
in the prompt (or install it in the run's workspace for an agentic run), keep stdin closed (`</dev/null`),
and set a timeout. Choose the judge from a different model family than the builder when the host offers
one (`cursor-agent --list-models`, `opencode models`). `codex exec` refuses a directory outside a git repository
unless given `--skip-git-repo-check`, and the judge and executor run in scratch directories, so always pass it.
Verified: Cursor and Claude Code.

## Subagents

All four hosts run parallel subagents: Claude Code's Agent tool, Cursor's subagents, Codex subagents (it
delegates when a skill asks it to), OpenCode's Task tool. The factory names roles, not host types: a
**read-only researcher** (2.1), a **general-purpose phase worker** for Phases 3, 4 and 5.0-5.1 (SKILL.md
"Orchestration") and a **general-purpose verifier** per panel role (5.2). A subagent cannot spawn subagents on
Claude Code or Cursor, so researchers and verifiers are always spawned by the main session, never by a worker.
Where a host offers none (or a run disables them), run each role, phase workers included, as a fresh headless
session from the table above, one after another, giving it only that role's inputs (a worker: its dispatch prompt);
that keeps the context walls the panel depends on and the main session's context small.

## Kill guard

`hooks/kill_guard.py` reads either contract and blocks pattern kills: `pkill`, `killall`, and any call holding
both a process lister (`pgrep`, `pidof`, `lsof`, `ps`) and a kill, however the PIDs travel (pipe, variable, renamed
file). The one exception: every kill names literal PIDs and nothing keeps the listing (`ps aux | head; kill 1234`
passes; `ps aux > f; kill 4242` and `ps aux | head; kill $PID` do not: kill in a call of its own). A redirect counts
wherever it is, a heredoc body included, so write a file that mentions both words with the editor tool. `fuser -k`
and `lsof … | xargs kill` are blocked too. It is a best-effort tripwire for honest mistakes, not a sandbox: it sees one call at a time, so a listing
saved in one call and killed in the next passes. `run_owned.py` and the rule (stop only what you started, by its
recorded PID) are the contract. The hook configs run it through `hooks/kill-guard`, which picks `python3`,
then `python`; with none, or when the hook fails (`"onFailure": "continue"` on Claude Code), it lets the
call through.

| Host | Ships as | Install | Status |
|---|---|---|---|
| Claude Code | `hooks/hooks.json` (`PreToolUse`, matcher `Bash`) | plugin install, or `claude --plugin-dir <skill-dir>` | verified live (`claude -p --plugin-dir`: blocked a `killall`, ran the next command) |
| Cursor | `.cursor-plugin/plugin.json` → `hooks/cursor-hooks.json` (`beforeShellExecution`) | plugin install; or for an `npx skills` install add the hook to `.cursor/hooks.json` (below) | verified live as a plugin (`cursor-agent --plugin-dir <skill-dir>`: `${CURSOR_PLUGIN_ROOT}` expanded, a `killall` was rejected, the next command ran) and as a project hook |
| Codex | the same script on `PreToolUse` | add to `~/.codex/hooks.json` or `<repo>/.codex/hooks.json` (below), then trust it in `/hooks`: Codex skips an untrusted hook | contract tested offline; untested in Codex |
| OpenCode | `hooks/opencode-kill-guard.js` (`tool.execute.before`) | symlink it into `.opencode/plugins/` or `~/.config/opencode/plugins/` | plugin tested with node; untested in OpenCode |

Cursor, for a skill installed at `<skill-dir>` (a project hook runs from the project root, a user hook from
`~/.cursor/`, so use the absolute path):

```json
{ "version": 1, "hooks": { "beforeShellExecution": [
  { "command": "sh <skill-dir>/hooks/kill-guard", "timeout": 10 } ] } }
```

Codex (for a vetted headless run, `codex exec --dangerously-bypass-hook-trust` runs enabled hooks without the
`/hooks` trust step), with the finish guard:

```json
{ "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [
  { "type": "command", "command": "sh <skill-dir>/hooks/kill-guard", "timeout": 10 } ] } ],
  "Stop": [ { "hooks": [ { "type": "command", "command": "sh <skill-dir>/hooks/finish-guard", "timeout": 30 } ] } ] } }
```

For an `npx skills` install on Cursor, the project hook above gains
`"stop": [ { "command": "sh <skill-dir>/hooks/finish-guard", "timeout": 30, "loop_limit": 1 } ]`.

## Finish guard

`hooks/finish_guard.py` runs at the end of each turn, through `hooks/finish-guard` (the same Python pick as the kill
guard; with none, the turn ends). Only in a factory build — the session's directory, or a `builds/<name>/` under it,
holding `work/evaluation/` and `output/<skill>/SKILL.md` — it runs `check_gate.py --finish`, and when that fails it
sends the agent back once with the reason, logged to `work/experiments/finish-guard.jsonl`. It never sends it back twice
in a row, and ends the turn on any error of its own. An interactive build costs one extra reply per stop from Phase 3 on:
the agent re-ends the turn on its question.

It guards only the main session's turn. A phase worker's, researcher's or panel member's stop never triggers it:
Claude Code ends a subagent on `SubagentStop` and Cursor on `subagentStop`, events the shipped configs do not
register, and the script also ignores either event and any payload carrying Claude Code's `agent_id`; the OpenCode
plugin skips child sessions (a `parentID`). Verified live on Claude Code 2.1.306 (`claude -p --plugin-dir`, a
Haiku main session spawning one subagent in an unfinished build: one `finish-guard.jsonl` entry, the main
session's); offline for Cursor and OpenCode; Codex subagent stops untested.

| Host | Ships as | Loop guard | Status |
|---|---|---|---|
| Claude Code | `hooks/hooks.json` `Stop` (plugin install, or `claude --plugin-dir <skill-dir>`) | `stop_hook_active`; Claude Code also caps a turn at 8 continuations | verified live (`claude -p --plugin-dir`: sent back once, the next stop ended the turn) |
| Cursor | `hooks/cursor-hooks.json` `stop` (`followup_message`) | `loop_count`, and `loop_limit: 1` | contract tested offline; `cursor-agent -p` (2026.10.18) runs no `stop` hook, so a headless Cursor build is not covered |
| Codex | the same script on `Stop` (`{"decision": "block"}`), installed like the kill guard below | `stop_hook_active` | contract tested offline; untested in Codex |
| OpenCode | `hooks/opencode-finish-guard.js` on `session.idle`, which prompts the session once (`client.session.prompt`) | once per idle | plugin tested with node; untested in OpenCode |

## Operating systems

The bundled scripts need Python 3.9+ and its standard library, nothing else, on macOS, Linux or WSL; run them
as `python3 <script>`. Native Windows is not supported: run the factory inside WSL (every script exits early
there with that message). `run_owned.py` owns a job through its process group, and `stop` ends only that group.
Verified: macOS and Linux locally and in CI.

## What needs Claude Code

- **`claude plugin eval`** (3.6 suite, 5.0 plugin path). Elsewhere the gate runs the manual protocol with no
  `claude` call; `gate.py` picks the path by phase-3.md 3.6's rule: plugin eval only inside Claude Code
  (which sets `$CLAUDECODE`), else manual, even where `claude` is installed, since the build targets its own
  host's model. **A Claude Code build never passes `--backend manual`**: `gate.py` picks manual itself where
  plugin eval cannot run or the skill is user-invoked, and refuses it (exit 3) for a model-invoked skill plugin eval
  can run, since manual mode there spends a headless session per case, arm and run. **Only another host whose
  shell inherits `$CLAUDECODE`** (a host CLI launched from a Claude Code shell: a benchmark, a nested session) passes
  `--backend manual --force-manual "<host>: <why>"`, e.g. `--force-manual "cursor-agent: launched from a Claude
  Code shell"`; plugin mode would otherwise run the gate on Claude, not the host's model. The reason is logged in
  `gate.json` (`force_manual`) and data-split.yaml; `gate.py` warns when plugin mode gets a model id that names no
  Claude model.
- **skill-creator's description optimizer** (4.6) measures triggering through `claude -p`. Elsewhere revise the
  description by hand against the same eval-set queries and log that no trigger rate was measured.
- **The reserved words `anthropic`/`claude`** in a skill `name` are a Claude platform rule; the 3.3 checklist
  keeps them on every host so a skill stays portable.
