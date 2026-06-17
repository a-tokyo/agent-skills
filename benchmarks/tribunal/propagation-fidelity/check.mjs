#!/usr/bin/env node
// Propagation-fidelity check for the tribunal skill.
//
// Tribunal v0.0.2 added "operative skill" propagation: when the orchestrator runs
// under a domain/quality skill (e.g. production-grade), it must forward that skill
// BY NAME, with a load instruction and a degrade note, to BOTH the doer and every
// verifier — and must NEVER forward the tribunal skill itself (no nesting).
//
// task1/task2 measure verification QUALITY; they exercise dispatch but never assert
// that the operative skill actually reached the subagents. This checker closes that
// gap by inspecting the prompts the orchestrator actually dispatched.
//
// Usage:
//   node check.mjs <dispatched-dir> [--skill production-grade]
//
// <dispatched-dir> must contain:
//   doer.txt            the prompt the orchestrator sent the doer
//   verifier-*.txt      one file per verifier prompt (at least one)
//
// Scoring is deterministic. Prints METRIC lines (harness METRIC protocol) and a
// PASS/FAIL summary; exits non-zero if any assertion fails.

import { readFileSync, readdirSync } from "node:fs";
import { join, basename } from "node:path";

const args = process.argv.slice(2);
const dir = args.find((a) => !a.startsWith("--"));
const skillIdx = args.indexOf("--skill");
const SKILL = skillIdx >= 0 ? args[skillIdx + 1] : "production-grade";

if (!dir) {
  console.error("usage: node check.mjs <dispatched-dir> [--skill <name>]");
  process.exit(2);
}

// A prompt "names the operative skill with a load+degrade instruction" when it
// mentions the skill by name, tells the subagent to LOAD it, and includes a
// degrade clause for the can't-load case. Matching is intentionally lenient on
// wording (any of several phrasings) and strict on presence.
const namesSkill = (t) => new RegExp(`\\b${escapeRe(SKILL)}\\b`, "i").test(t);
const hasLoad = (t) => /\b(load|install|use|apply)\b/i.test(t);
const hasDegrade = (t) =>
  /(if (you )?(can'?t|cannot)|unable to load|if .*not available|say so|report (that|it)|flag (that|it))/i.test(t);
// "no nesting": the prompt must not instruct the subagent to load/run the tribunal
// skill itself. A bare mention (e.g. "you are the doer in a tribunal run") is fine;
// an instruction to load/use the tribunal skill is a nesting violation.
const forwardsTribunal = (t) =>
  /\b(load|install|use|run|apply)\b[^.\n]{0,40}\btribunal\b(?!\s+(pattern|run|loop|protocol))/i.test(t) ||
  /\btribunal\b[^.\n]{0,20}\bskill\b[^.\n]{0,40}\b(load|install|use|apply)\b/i.test(t);

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

let files;
try {
  files = readdirSync(dir);
} catch (e) {
  console.error(`cannot read dispatched dir: ${dir} (${e.message})`);
  process.exit(2);
}

const doerFile = files.find((f) => /^doer\b.*\.txt$/i.test(f));
const verifierFiles = files.filter((f) => /^verifier[-_].*\.txt$/i.test(f)).sort();

const failures = [];
const note = (cond, msg) => {
  if (!cond) failures.push(msg);
  return cond;
};

// --- doer ---
let doerOk = false;
if (note(!!doerFile, "no doer.txt found in dispatched dir")) {
  const t = readFileSync(join(dir, doerFile), "utf8");
  const n = note(namesSkill(t), `doer prompt does not name operative skill "${SKILL}"`);
  const l = note(n && hasLoad(t), "doer prompt does not instruct loading the operative skill");
  const d = note(l && hasDegrade(t), "doer prompt lacks a degrade note (what to do if it can't load)");
  note(!forwardsTribunal(t), "doer prompt forwards the tribunal skill itself (nesting violation)");
  doerOk = n && l && d;
}

// --- verifiers ---
note(verifierFiles.length >= 1, "no verifier-*.txt files found in dispatched dir");
let verifiersOk = verifierFiles.length >= 1;
for (const f of verifierFiles) {
  const t = readFileSync(join(dir, f), "utf8");
  const id = basename(f);
  const n = note(namesSkill(t), `${id}: does not name operative skill "${SKILL}"`);
  const l = note(n && hasLoad(t), `${id}: does not instruct loading the operative skill`);
  const d = note(l && hasDegrade(t), `${id}: lacks a degrade note`);
  note(!forwardsTribunal(t), `${id}: forwards the tribunal skill itself (nesting violation)`);
  verifiersOk = verifiersOk && n && l && d;
}

const nestingClean = !failures.some((m) => /nesting violation/.test(m));
const pass = failures.length === 0;

// METRIC protocol lines (name=value) for deterministic harness extraction.
console.log(`METRIC doer_propagation=${doerOk ? 1 : 0}`);
console.log(`METRIC verifier_count=${verifierFiles.length}`);
console.log(`METRIC verifier_propagation=${verifiersOk ? 1 : 0}`);
console.log(`METRIC no_nesting=${nestingClean ? 1 : 0}`);
console.log(`METRIC propagation_fidelity=${pass ? 1 : 0}`);

if (pass) {
  console.log(`\nPASS — operative skill "${SKILL}" propagated to doer + ${verifierFiles.length} verifier(s); no nesting.`);
  process.exit(0);
} else {
  console.log(`\nFAIL (${failures.length}):`);
  for (const m of failures) console.log(`  - ${m}`);
  process.exit(1);
}
