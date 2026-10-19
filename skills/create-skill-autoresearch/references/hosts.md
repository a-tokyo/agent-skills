# Hosts

The factory is an Agent Skill: any agent that loads `SKILL.md` runs it. This file names what each host
calls the pieces the pipeline uses. Wherever SKILL.md says **the host's headless CLI**, use the
Headless row for your host. Facts from each host's official docs, read on 2026-10-19
([Claude Code](https://code.claude.com/docs/en/skills), [Cursor](https://cursor.com/docs/skills),
[Codex](https://developers.openai.com/codex/skills), [OpenCode](https://opencode.ai/docs/skills/)); "verified"
marks what the factory's own tests or benchmark ran.

## Contents
- Skills and install
- Headless runs, judges and executors
- Subagents
- Kill guard
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
| Codex | `codex exec "<prompt>"` | `-m <id>` | `--sandbox workspace-write` | `--json` |
| OpenCode | `opencode run "<prompt>"` | `-m <provider/model>` | `--auto` | `--format json` |

The same command serves every headless role: the 3.4 executor and judge, the manual-protocol agentic
executor and `llm` grader (benchmark-standard.md), and a fresh-session subagent. Pass the skill as context
in the prompt (or install it in the run's workspace for an agentic run), keep stdin closed (`</dev/null`),
and set a timeout. Choose the judge from a different model family than the builder when the host offers
one (`cursor-agent --list-models`, `opencode models`). Verified: Cursor and Claude Code.

## Subagents

All four hosts run parallel subagents: Claude Code's Agent tool, Cursor's subagents, Codex subagents (it
delegates when a skill asks it to), OpenCode's Task tool. The factory names roles, not host types: a
**read-only researcher** (2.1) and a **general-purpose verifier** per panel role (5.2). Where a host offers
none (or a run disables them), run each role as a fresh headless session from the table above, one after
another, giving it only that role's inputs; that keeps the context walls the panel depends on.

## Kill guard

`hooks/kill_guard.py` reads either contract and blocks `pkill`, `killall` and pgrep-fed kills; `run_owned.py`
is the guard where none of these is installed.

| Host | Ships as | Install | Status |
|---|---|---|---|
| Claude Code | `hooks/hooks.json` (`PreToolUse`, `Bash`) | plugin install, or `claude --plugin-dir <skill-dir>` | verified (plugin load, smoke run) |
| Cursor | `.cursor-plugin/plugin.json` → `hooks/cursor-hooks.json` (`beforeShellExecution`) | plugin install; or for an `npx skills` install add the hook to `.cursor/hooks.json` (below) | contract tested offline; verified live as a project hook under `cursor-agent` (blocked a `pkill`, allowed the next command); the plugin install path is untested |
| Codex | the same script on `PreToolUse` | add to `~/.codex/hooks.json` or `<repo>/.codex/hooks.json` (below); Codex asks you to trust hooks | contract tested offline; untested in Codex |
| OpenCode | `hooks/opencode-kill-guard.js` (`tool.execute.before`) | symlink it into `.opencode/plugins/` or `~/.config/opencode/plugins/` | plugin tested with node; untested in OpenCode |

Cursor, for a skill installed at `<skill-dir>` (a project hook runs from the project root, a user hook from
`~/.cursor/`, so use the absolute path):

```json
{ "version": 1, "hooks": { "beforeShellExecution": [
  { "command": "python3 <skill-dir>/hooks/kill_guard.py", "timeout": 10 } ] } }
```

Codex:

```json
{ "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [
  { "type": "command", "command": "python3 <skill-dir>/hooks/kill_guard.py", "timeout": 10 } ] } ] } }
```

## What needs Claude Code

- **`claude plugin eval`** (3.6 suite, 5.0 plugin path). Elsewhere the gate runs the manual protocol with no
  `claude` call; `gate.py` decides by probing `claude plugin eval --help` (absent → manual).
- **skill-creator's description optimizer** (4.6) measures triggering through `claude -p`. Elsewhere revise the
  description by hand against the same eval-set queries and log that no trigger rate was measured.
- **The reserved words `anthropic`/`claude`** in a skill `name` are a Claude platform rule; the 3.3 checklist
  keeps them on every host so a skill stays portable.
