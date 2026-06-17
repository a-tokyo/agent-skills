#!/usr/bin/env node
// Offline self-test for check.mjs — no API, no network. Confirms the checker
// PASSes a correctly-propagated dispatch and FAILs the two seeded regressions.
// Run: node selftest.mjs   (exit 0 = the checker itself is sound)

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const check = join(here, "check.mjs");

const cases = [
  { dir: "fixtures/pass", expect: 0, why: "operative skill propagated to doer + both verifiers" },
  { dir: "fixtures/fail-doer-missing", expect: 1, why: "doer prompt omits the operative skill" },
  { dir: "fixtures/fail-nesting", expect: 1, why: "doer forwards the tribunal skill (nesting)" },
];

let bad = 0;
for (const c of cases) {
  let code = 0;
  let out = "";
  try {
    out = execFileSync("node", [check, join(here, c.dir)], { encoding: "utf8" });
  } catch (e) {
    code = e.status ?? 1;
    out = (e.stdout || "") + (e.stderr || "");
  }
  const ok = code === c.expect;
  if (!ok) bad++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${c.dir} -> exit ${code} (expected ${c.expect}) — ${c.why}`);
  if (!ok) console.log(out);
}

if (bad) {
  console.log(`\n${bad} self-test case(s) failed — the checker is not sound.`);
  process.exit(1);
}
console.log("\nAll self-test cases passed — check.mjs is sound.");
