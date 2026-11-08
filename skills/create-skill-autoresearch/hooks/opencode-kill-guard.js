// OpenCode plugin: the create-skill-autoresearch kill guard (references/hosts.md). Symlink it into
// .opencode/plugins/ or ~/.config/opencode/plugins/ (or set CSA_KILL_GUARD) so it runs ./kill_guard.py beside
// its real path. Before every bash call it sends the command as a PreToolUse event, with the session's directory
// (the plugin's `directory`) as cwd, to the first Python 3 that runs; exit 2 blocks the call by throwing. Before
// every read call it sends the file to ./read_guard.py the same way (the read guard: no script or hook source;
// OpenCode gives no transcript, so no duplicate-read check), and every edit, write or patch call to ./freeze_guard.py
// (a build's output/<skill>/ after the panel, outside a fix cycle). No Python: the call goes through with a warning. OpenCode treats every export as a plugin, so this module exports
// only KillGuard.
import { execFileSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GUARD = process.env.CSA_KILL_GUARD || join(dirname(realpathSync(fileURLToPath(import.meta.url))), "kill_guard.py");
const READ_GUARD = join(dirname(GUARD), "read_guard.py");
const FREEZE_GUARD = join(dirname(GUARD), "freeze_guard.py");
const WRITERS = new Set(["edit", "write", "patch", "multiedit"]);
const PYTHONS = [["python3"], ["python"]];

function check(command, cwd) {
  return run(GUARD, { tool_name: "Bash", tool_input: { command }, cwd });
}

function run(guard, event) {
  const input = JSON.stringify(event);
  for (const [bin, ...args] of PYTHONS) {
    try {
      execFileSync(bin, [...args, guard], { input, stdio: ["pipe", "pipe", "pipe"] });
      return null;
    } catch (error) {
      if (error.status === 2) return String(error.stderr).trim();
    }
  }
  console.warn("kill guard: no Python 3 ran it (python3, python); call allowed");
  return null;
}

export const KillGuard = async (ctx = {}) => {
  const cwd = ctx?.directory ?? ctx?.worktree ?? process.cwd();
  if (!existsSync(GUARD)) {
    throw new Error(`kill guard: ${GUARD} not found; symlink this plugin from the skill's hooks/ or set CSA_KILL_GUARD`);
  }
  return {
    "tool.execute.before": async (input, output) => {
      let reason = null;
      if (input.tool === "bash") reason = check(output.args?.command ?? "", cwd);
      else if (input.tool === "read") {
        const { filePath, offset, limit } = output.args ?? {};
        reason = run(READ_GUARD, { tool_name: "Read", tool_input: { file_path: filePath ?? "", offset, limit }, cwd });
      } else if (WRITERS.has(input.tool)) {
        const { filePath } = output.args ?? {};
        reason = run(FREEZE_GUARD, { tool_name: "Write", tool_input: { file_path: filePath ?? "" }, cwd });
      }
      if (reason) throw new Error(reason);
    },
  };
};
