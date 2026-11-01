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
- Gate wait
- Poll guard
- Kill guard (and the loop guard)
- Read guard
- Subagent guard
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
and set a timeout. An executor or judge on Claude Code or Cursor also gets `--plugin-dir <skill-dir>` and
`CSA_EXECUTOR=1` (phase-3.md 3.4): the kill guard loads, the finish and subagent guards stay inert; on Codex and
OpenCode a user-level hook install already covers it. Choose the judge from a different model family than the builder when the host offers
one (`cursor-agent --list-models`, `opencode models`). `codex exec` refuses a directory outside a git repository
unless given `--skip-git-repo-check`, and the judge and executor run in scratch directories, so always pass it.
Verified: Cursor and Claude Code.

## Subagents

All four hosts run parallel subagents: Claude Code's Agent tool, Cursor's subagents, Codex subagents (it
delegates when a skill asks it to), OpenCode's Task tool. The factory names roles, not host types: a
**read-only researcher** (2.1) and a **general-purpose verifier** per panel role (5.2); the main session runs
every phase itself and spawns both. Where a host offers none (or a run disables them), run each role as a fresh
headless session from the table above, one after another, giving it only that role's inputs; that keeps the
context walls the panel depends on.

## Gate wait

The 5.0 gate is one foreground shell call: `gate.py --detach --wait` starts the owned job `gate` and blocks up to
570 s (`--wait-timeout`), then exits 124 if it is still running; the job keeps running either way, and re-issuing
the same command waits on it (never `--restart`, which starts it over). In the 2026-11-01 r18 matrix 59 of 60
final gates ended inside 570 s, so one call is enough when the shell call may wait that long. What cut it short
was the shell tool, not the gate: Haiku builders left Claude Code's Bash timeout at its 120000 ms default, then
shrank `--wait-timeout` to fit it, and polled (26 exit-124 calls over 30 builds).

| Host | One call | Status |
|---|---|---|
| Claude Code | Bash `timeout: 600000` (the tool's maximum; default 120000), default `--wait-timeout`, foreground. A `run_in_background` call notifies on completion, but a builder that ends its turn to wait is sent back by the finish guard and loses a turn | measured on r18 transcripts |
| Cursor, Codex, OpenCode | give the shell call its longest timeout and `--wait-timeout` 30 s under it; re-issue the same command on 124 | untested; their shell limits are not recorded here |

`check_gate.py --next` prints the command with this note, and a still-running exit repeats it in one line.
`rescore.py` and `run_owned.py start <name> --wait` block and re-attach the same way (owned jobs `rescore` and
`<name>`). On Claude Code the kill guard's PreToolUse hook gives every call to this factory's scripts or a build's
`work/evaluation/*.sh` `timeout` 600000 and `run_in_background` false itself, through `updatedInput` (verified
against the 2.1.x source: the hook's object replaces the tool input, so it echoes the other fields). Live check on
2026-11-01 (Claude Code 2.1.316, Haiku 5.5, no timeout in the call): a 130 s `evaluate.sh` finished in the foreground
with the plugin loaded, and without it moved to the background at 120 s. A host that ignores `updatedInput` keeps
the call's own timeout, and the scripts' exit 124 line says to issue the command again.

## Poll guard

While a job the build owns runs (`gate`, `rescore`, a `run_owned.py start <name> --wait` run: a running record in
`work/pids/`), the kill guard denies the build's main session a Bash sleep or poll loop (`sleep N`, `until ...; do`,
`while ...; do ... sleep`, `while kill -0`) and, on Claude Code, the Monitor tool (its own `PreToolUse` matcher in
`hooks/hooks.json`). The message names the command that blocks on that job (`gate.py --wait`, `rescore.py`,
`run_owned.py wait <name>`). r19 Haiku builders polled a running gate with Monitor or sleep loops dozens of times a
build, each poll re-reading the whole context. Subagents (`agent_id`) and executor sessions (`CSA_EXECUTOR`) may poll.

| Host | Hook | Status |
|---|---|---|
| Claude Code | the kill guard on `Bash` and `Monitor` | tested offline |
| Cursor | the kill guard's `beforeShellExecution` (shell polls only) | contract tested offline |
| Codex | the kill guard's `PreToolUse` (shell polls only) | contract tested offline; untested in Codex |

## Kill guard

`hooks/kill_guard.py` reads either contract and acts only in a factory build — the call's cwd (else the hook's
own directory), or a `builds/<name>/` under it, holding `work/manifest.yaml` or `work/evaluation/` — or an executor
session (`CSA_EXECUTOR=1`); anywhere else it allows every call. There it blocks pattern kills: `pkill`, `killall`, and any call holding
both a process lister (`pgrep`, `pidof`, `lsof`, `ps`) and a kill, however the PIDs travel (pipe, variable, renamed
file). The one exception: every kill names literal PIDs and nothing keeps the listing (`ps aux | head; kill 1234`
passes; `ps aux > f; kill 4242` and `ps aux | head; kill $PID` do not: kill in a call of its own). A redirect counts
wherever it is, a heredoc body included, so write a file that mentions both words with the editor tool. `fuser -k`
and `lsof … | xargs kill` are blocked too. It is a best-effort tripwire for honest mistakes, not a sandbox: it sees one call at a time, so a listing
saved in one call and killed in the next passes. `run_owned.py` and the rule (stop only what you started, by its
recorded PID) are the contract. The hook configs run it through `hooks/kill-guard`, which picks `python3`,
then `python`; with none, or when the hook fails (`"onFailure": "continue"` on Claude Code), it lets the
call through.

**Loop guard.** The same hook denies a call that runs a build's Phase 4 metric — `work/evaluation/evaluate.sh` in
command position with no case argument, resolved against the call's `cd` or the session's directory — while
`check_gate.py --loop` says the loop is done (phase-4.md 4.3), with "Phase 4 is done (<reason>); go to 5.0".
`evaluate.sh <case>` always runs (the 4.2 held-out score), as does every call in an executor session
(`CSA_EXECUTOR=1`) or one whose path it cannot resolve (`$WS/…`). OpenCode sends no directory of its own: its
plugin passes the session's `directory` as the cwd. Best effort, like the kill guard.

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
`"stop": [ { "command": "sh <skill-dir>/hooks/finish-guard", "timeout": 30, "loop_limit": 1 } ]` and
`"preToolUse": [ { "command": "sh <skill-dir>/hooks/finish-guard --subagent", "matcher": "Task|CallDynamicTool", "timeout": 10 } ]`
and `"beforeReadFile": [ { "command": "sh <skill-dir>/hooks/read-guard", "timeout": 10 } ]`.

## Read guard

`hooks/read_guard.py` keeps the factory's own files out of a build's context. It denies reading this skill's
`scripts/*.py` and `hooks/*` source (each script's `--help` says how to run it), and a second whole read of a
`references/` file in one context: the first copy is still there. A context compaction ends that context, so the
file reads again after one (Claude Code's transcript marks it `compact_boundary`). It sees the Read tool on its own
hook and shell reads (`cat`, `sed`, `head`, `tail`, `less`, ...) through the kill guard, after the call's own
`NAME=value` and `cd`. Like the kill guard it acts only in a build session, and it never denies a path outside the
skill's directory: the skill being built, the task materials and `work/` always read. A ranged read (`offset`,
`sed -n 10,40p`) of a reference is allowed. Any error of its own allows the read.

| Host | Ships as | Duplicate check | Status |
|---|---|---|---|
| Claude Code | `hooks/hooks.json` `PreToolUse`, matcher `Read` (exit 2, reason on stderr); shell reads via the kill guard | the event's `transcript_path`; a subagent (`agent_id`) against its own `subagents/agent-<id>.jsonl` | verified live (`claude -p --plugin-dir`, 2026-11-01: denied `scripts/check_gate.py` and a second `phase-5.md` read, allowed the first and `work/`); subagents offline-tested |
| Cursor | `hooks/cursor-hooks.json` `beforeReadFile` (`{"permission": "deny", ...}`); shell reads via `beforeShellExecution` | none: no transcript in the event, so reference reads are allowed | contract from the hooks doc, offline-tested; unverified live |
| Codex | the kill guard's `PreToolUse` script sees shell reads only | when the event carries `transcript_path` in Claude's format | contract tested offline; untested in Codex |
| OpenCode | `hooks/opencode-kill-guard.js` on `read` and `bash` | none: no transcript | plugin tested with node; untested in OpenCode |

## Subagent guard

`hooks/finish_guard.py --subagent` (through the same launcher) runs before each subagent dispatch. Only in a factory
build past Phase 1 — the session's directory, or a `builds/<name>/` under it, holding `work/manifest.yaml` or
`work/evaluation/` (looser than the finish guard's test, because Phase 2 researchers are
dispatched before `output/<skill>/SKILL.md` exists) — it denies a subagent that would run in the background, with
the rule: dispatch researchers and panel roles in the foreground, and for parallel roles put several Agent calls in
one message (they run in parallel and the turn waits for all). Why: headless `claude -p` stops background tasks 10
minutes after the last turn, so a main session that dispatches a subagent in the background and ends its turn to wait
abandons the build (3 of 20 Haiku task-2 builds in the 2026-10-22 matrix). Input it cannot read, an error of its
own, or `CSA_EXECUTOR` in its environment (an executor or judge session, phase-3.md 3.4) lets the subagent start.

| Host | Ships as | Denies | Status |
|---|---|---|---|
| Claude Code | `hooks/hooks.json` `PreToolUse`, matcher `Agent\|Task` (exit 2, reason on stderr) | `Agent` unless `run_in_background: false` (the Agent tool defaults to the background: 98 of the matrix's 220 background dispatches omitted the flag); a legacy `Task` only with `run_in_background: true` | verified live on Claude Code 2.1.306 (`claude -p --plugin-dir`, Haiku, a fake build holding `work/manifest.yaml`): `run_in_background: true` and an absent flag both denied, each retry with `false` ran |
| Cursor | `hooks/cursor-hooks.json` `preToolUse`, matcher `Task\|CallDynamicTool` (`{"permission": "deny", ...}`) | a Task call with `run_in_background: true` (Cursor's Task runs in the foreground unless asked; its transcripts record `CallDynamicTool {toolName: Task, arguments, run_in_background}`, so both places are read) | contract from the hooks doc (read 2026-10-22), offline-tested; the payload shape is unverified live |
| Codex, OpenCode | not shipped | — | not checked whether their subagent tools run in the background; the foreground rule in SKILL.md still applies |

## Finish guard

`hooks/finish_guard.py` runs at the end of each turn, through `hooks/finish-guard` (the same Python pick as the kill
guard; with none, the turn ends). Only in a factory build — the session's directory, or a `builds/<name>/` under it,
holding `work/evaluation/` and `output/<skill>/SKILL.md` — it runs `check_gate.py --finish`, and when that fails it
sends the agent back with the reason, logged to `work/experiments/finish-guard.jsonl`. It never sends it back twice
in a row, and ends the turn on any error of its own or with `CSA_EXECUTOR` in its environment (an executor or
judge session started at the build root, phase-3.md 3.4). That log is also its count: after `max_finish_sendbacks`
(verdict-rules.json, 3) send-backs to a build, the next stop ends the turn with one stderr line saying the build ends
unfinished (`check_gate.py --finish` still fails it), which bounds an alternating stop/continue loop (one Haiku
build was sent back 30 times). Phases 1-2 are never sent back or counted (no `output/<skill>/SKILL.md` yet); from
Phase 3 an interactive build costs one extra reply per stop, the agent re-ending the turn on its question, and after
the cap those stops end quietly.

It guards only the main session's turn. A researcher's or panel member's stop never triggers it:
Claude Code ends a subagent on `SubagentStop` and Cursor on `subagentStop`, events the shipped configs do not
register, and the script also ignores either event and any payload carrying Claude Code's `agent_id`; the OpenCode
plugin skips child sessions (a `parentID`). Verified live on Claude Code 2.1.306 (`claude -p --plugin-dir`, a
Haiku main session spawning one subagent in an unfinished build: one `finish-guard.jsonl` entry, the main
session's); offline for Cursor and OpenCode; Codex subagent stops untested.

| Host | Ships as | Loop guard | Status |
|---|---|---|---|
| Claude Code | `hooks/hooks.json` `Stop` (plugin install, or `claude --plugin-dir <skill-dir>`) | `stop_hook_active`; Claude Code also caps a turn at 8 continuations; `max_finish_sendbacks` per build (every host) | verified live (`claude -p --plugin-dir`: sent back once, the next stop ended the turn) |
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
