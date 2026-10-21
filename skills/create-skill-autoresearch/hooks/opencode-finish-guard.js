// OpenCode plugin: the create-skill-autoresearch finish guard (references/hosts.md).
//
// OpenCode has no blocking end-of-turn hook; it emits `session.idle` when a turn ends. On that event this
// plugin sends ./finish_guard.py (beside its real path; symlink it into .opencode/plugins/ or
// ~/.config/opencode/plugins/, or set CSA_FINISH_GUARD) a Claude Code Stop event for the session's
// directory. When the guard answers "block", it prompts the session once with the guard's reason; the next
// idle of that session ends the turn whatever the guard says, so it never loops. No Python, or any error:
// the turn ends. A child session (a subagent's: its session has a parentID) is never prompted; when the
// lookup fails the session is treated as top-level.
import { execFileSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GUARD = process.env.CSA_FINISH_GUARD || join(dirname(realpathSync(fileURLToPath(import.meta.url))), "finish_guard.py");
const PYTHONS = process.platform === "win32" ? [["py", "-3"], ["python"], ["python3"]] : [["python3"], ["python"]];

export const FinishGuard = async ({ client, directory }) => {
  if (!existsSync(GUARD)) {
    throw new Error(`finish guard: ${GUARD} not found; symlink this plugin from the skill's hooks/ or set CSA_FINISH_GUARD`);
  }
  const sentBack = new Set();
  return {
    event: async ({ event }) => {
      if (event?.type !== "session.idle") return;
      const id = event.properties?.sessionID;
      if (!id) return;
      if (sentBack.delete(id)) return;
      try {
        const info = await client.session.get({ path: { id } });
        if ((info?.data ?? info)?.parentID) return;
      } catch {
        // no session info: treat it as the top-level session
      }
      const input = JSON.stringify({ hook_event_name: "Stop", cwd: directory, stop_hook_active: false });
      for (const [bin, ...args] of PYTHONS) {
        let reply;
        try {
          reply = JSON.parse(execFileSync(bin, [...args, GUARD], { input, encoding: "utf8", windowsHide: true, timeout: 30000 }));
        } catch {
          continue;
        }
        if (reply?.decision === "block" && reply.reason) {
          sentBack.add(id);
          client.session.prompt({ path: { id }, body: { parts: [{ type: "text", text: reply.reason }] } }).catch(() => {});
        }
        return;
      }
    },
  };
};
