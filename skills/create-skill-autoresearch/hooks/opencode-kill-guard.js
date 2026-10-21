// OpenCode plugin: the create-skill-autoresearch kill guard (references/hosts.md). Symlink it into
// .opencode/plugins/ or ~/.config/opencode/plugins/ (or set CSA_KILL_GUARD) so it runs ./kill_guard.py beside
// its real path. Before every bash call it sends the command as a PreToolUse event to the first Python 3 that
// runs; exit 2 blocks the call by throwing. No Python: the call goes through with a warning. OpenCode treats
// every export as a plugin, so this module exports only KillGuard.
import { execFileSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GUARD = process.env.CSA_KILL_GUARD || join(dirname(realpathSync(fileURLToPath(import.meta.url))), "kill_guard.py");
const PYTHONS = [["python3"], ["python"]];

function check(command) {
  const input = JSON.stringify({ tool_name: "Bash", tool_input: { command } });
  for (const [bin, ...args] of PYTHONS) {
    try {
      execFileSync(bin, [...args, GUARD], { input, stdio: ["pipe", "pipe", "pipe"] });
      return null;
    } catch (error) {
      if (error.status === 2) return String(error.stderr).trim();
    }
  }
  console.warn("kill guard: no Python 3 ran it (python3, python); command allowed");
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
