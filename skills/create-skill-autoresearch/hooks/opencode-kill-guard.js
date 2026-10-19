// OpenCode plugin: the create-skill-autoresearch kill guard (references/hosts.md).
//
// OpenCode loads plugins from .opencode/plugins/ or ~/.config/opencode/plugins/. Symlink this file
// there rather than copying it: it runs ./kill_guard.py beside its real path, so detection lives in one
// place. Set CSA_KILL_GUARD to the script's path when the symlink is not possible.
//
// OpenCode treats every export of a plugin module as a plugin, so this module exports only KillGuard.
// Before every bash tool call it sends the command to kill_guard.py as a PreToolUse event, with the
// first Python 3 that runs (python3, python; on Windows py -3 first); exit 2 blocks the call by
// throwing, which OpenCode reports to the agent. If no Python runs, the call is let through with a
// warning: the guard must never stop every shell call.
import { execFileSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GUARD = process.env.CSA_KILL_GUARD || join(dirname(realpathSync(fileURLToPath(import.meta.url))), "kill_guard.py");
const PYTHONS = process.platform === "win32" ? [["py", "-3"], ["python"], ["python3"]] : [["python3"], ["python"]];

function check(command) {
  const input = JSON.stringify({ tool_name: "Bash", tool_input: { command } });
  for (const [bin, ...args] of PYTHONS) {
    try {
      execFileSync(bin, [...args, GUARD], { input, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
      return null;
    } catch (error) {
      if (error.status === 2) return String(error.stderr).trim();
    }
  }
  console.warn("kill guard: no Python 3 ran it (python3, python, py -3); command allowed");
  return null;
}

export const KillGuard = async () => {
  if (!existsSync(GUARD)) {
    throw new Error(`kill guard: ${GUARD} not found; symlink this plugin from the skill's hooks/ or set CSA_KILL_GUARD`);
  }
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash") return;
      const reason = check(output.args?.command ?? "");
      if (reason) throw new Error(reason);
    },
  };
};
