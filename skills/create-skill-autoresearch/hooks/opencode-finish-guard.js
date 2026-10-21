// OpenCode plugin: the create-skill-autoresearch finish guard (references/hosts.md). Symlink it like the kill
// guard (or set CSA_FINISH_GUARD). OpenCode has no blocking end-of-turn hook, so on `session.idle` of a
// top-level session (a subagent's has a parentID; a failed lookup counts as top-level) it sends
// ./finish_guard.py a Stop event and, on "block", prompts the session once with the reason; that session's
// next idle ends the turn whatever the guard says. No Python, or any error: the turn ends.
import { execFileSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GUARD = process.env.CSA_FINISH_GUARD || join(dirname(realpathSync(fileURLToPath(import.meta.url))), "finish_guard.py");
const PYTHONS = [["python3"], ["python"]];

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
          reply = JSON.parse(execFileSync(bin, [...args, GUARD], { input, encoding: "utf8", timeout: 30000 }));
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
