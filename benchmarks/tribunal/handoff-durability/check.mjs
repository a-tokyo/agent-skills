#!/usr/bin/env node
// Handoff-durability check for the tribunal skill.
//
// Tribunal v0.0.3 added three detached-runtime invariants: the doer materializes the
// artifact durably and reports a FETCHABLE ADDRESS; the orchestrator hands that address
// (not a working-tree path) to every verifier; and the round index + remaining budget
// travel in the handoff rather than in orchestrator context.
//
// propagation-fidelity/ measures whether an *operative skill* reaches the subagents.
// This checker measures the FORM OF THE HANDOFF in the same dispatched prompts — which
// is host-neutral and observable on any runtime, including synchronous ones with a
// shared filesystem (see README "Scope").
//
// Usage:
//   node check.mjs <dispatched-dir>
//
// <dispatched-dir> must contain:
//   doer.txt          the prompt the orchestrator sent the doer
//   doer-report.txt   the doer's report back to the orchestrator
//   verifier-*.txt    one file per verifier prompt (at least one)
//   ledger.txt|.md    OPTIONAL durable ledger; also scanned for the budget counters
//
// Scoring is deterministic. Prints METRIC lines (harness METRIC protocol) and a
// PASS/FAIL summary; exits non-zero if any assertion fails.
//
// ---------------------------------------------------------------------------
// WHAT COUNTS AS A "FETCHABLE ADDRESS" (decided up front, before the fixtures)
// ---------------------------------------------------------------------------
// An address is anything an agent in a DIFFERENT sandbox could retrieve on its own:
//
//   sha     a bare 40-hex commit id, or a 7-40 hex id introduced by an anchoring word
//           (commit / sha / hash / rev / revision / ref / HEAD / checkout / tree).
//           An abbreviated id must contain at least one DIGIT — that alone rejects
//           English words that happen to be hex ("acceded", "defaced") without
//           rejecting real short SHAs (a digit-free 7-hex SHA occurs ~0.1% of the time).
//   branch  `branch <name>`, `refs/heads/<name>`, `refs/tags/<name>`, `origin/<name>`
//   uri     https:// http:// ssh:// file:// s3:// gs:// or git@host:path
//   pr      `PR #123`, `MR !123`, `pull request #123`, or a `/pull/123` URL path —
//           the repo is implicit in a tribunal ledger, and this is how doers on real
//           runtimes most often name the artifact.
//
// EXPLICIT NON-RULE: a path never disqualifies anything. Real v0.0.3 doers report an
// address AND the touched paths, and verifiers need both. Every assertion below is
// presence-of-address; none is absence-of-path. (Asserting absence-of-path would fail
// every honest run — the mistake this comment exists to prevent.)
//
// A verifier "carries THE address" when it contains one of the addresses the doer
// reported. SHAs match by prefix in either direction at >= 7 chars, so a verifier
// quoting `a1b2c3d` satisfies a report that gave the full 40-hex id.
// ---------------------------------------------------------------------------

import { readFileSync, readdirSync } from "node:fs";
import { join, basename } from "node:path";

const args = process.argv.slice(2);
const dir = args.find((a) => !a.startsWith("--"));

if (!dir) {
  console.error("usage: node check.mjs <dispatched-dir>");
  process.exit(2);
}

// ---------- address recognition ----------

// Every regex below runs on WHITESPACE-NORMALIZED text (see `flat`). Real prompts wrap
// at ~90 columns, so any pattern anchored with [^\n] silently stops matching the moment
// a model breaks the line mid-phrase — the single biggest source of false FAILs here.
const flat = (t) => t.replace(/\s+/g, " ");

const RE_SHA40 = /\b[0-9a-f]{40}\b/gi;
const RE_SHA_ANCHORED =
  /\b(?:commit(?:s|ted|ting)?|sha|hash|rev|revision|ref|head|checkout|tree|push(?:es|ed)?)\b.{0,40}?\b([0-9a-f]{7,40})\b/gi;
const RE_BRANCH = /\b(?:refs\/(?:heads|tags)\/[\w.\/-]+|origin\/[\w.\/-]+)\b/gi;
const RE_BRANCH_WORD = /\b(?:branch|tag)\s+[`'"]?([\w][\w.\/-]*)[`'"]?/gi;
// "branch <word>" is also ordinary English ("the branch WE discussed", "the branch OF the
// tree"), so the captured word must actually look like a ref before it counts as an
// address: either it carries ref punctuation / a digit, or it is a conventional branch
// name. Without this, a v0.0.2 report that merely mentions a branch in passing scores as
// having reported an address — a false PASS on the metric that most separates the arms.
const CONVENTIONAL_BRANCHES = new Set([
  "main", "master", "develop", "dev", "trunk", "staging", "stage", "prod", "production",
  "release", "head",
]);
// `.` `/` `-` are legal inside a ref, so the capture class must include them — which means
// it also swallows the sentence's full stop ("on branch x." captures "x."). Trim trailing
// separators first, or every prose mention that ends a sentence acquires a "." and passes
// the ref test on that alone.
const trimRef = (name) => name.replace(/[./-]+$/, "");
const looksLikeRef = (raw) => {
  const name = trimRef(raw);
  return (
    name.length > 0 &&
    (/[/\-_.]/.test(name) || /\d/.test(name) || CONVENTIONAL_BRANCHES.has(name.toLowerCase()))
  );
};
const RE_URI = /\b(?:https?|ssh|file|s3|gs):\/\/\S+|\bgit@[\w.-]+:\S+/gi;
const RE_PR =
  /\b(?:PR|MR|pull request|merge request)\s*[#!]?\s*(\d+)\b|\/(?:pull|merge_requests)\/(\d+)/gi;

const hasDigit = (s) => /\d/.test(s);

// Returns [{ kind, value }] for every address-shaped token in `raw`.
function addressesIn(raw) {
  const t = flat(raw);
  const out = [];
  const push = (kind, value) => {
    if (value && !out.some((a) => a.kind === kind && a.value === value)) out.push({ kind, value });
  };

  for (const m of t.matchAll(RE_SHA40)) push("sha", m[0].toLowerCase());
  for (const m of t.matchAll(RE_SHA_ANCHORED)) {
    const id = m[1].toLowerCase();
    if (id.length === 40 || hasDigit(id)) push("sha", id);
  }
  for (const m of t.matchAll(RE_BRANCH)) push("branch", m[0]);
  for (const m of t.matchAll(RE_BRANCH_WORD)) if (looksLikeRef(m[1])) push("branch", trimRef(m[1]));
  // RE_URI ends in \S+, so it swallows whatever punctuation the prose wrapped it in.
  // Backticks and quotes matter most: models routinely write `<url>` in a report and the
  // bare url in a dispatch, and an unstripped backtick makes those two compare unequal.
  for (const m of t.matchAll(RE_URI)) push("uri", m[0].replace(/[`'"<>.,;:)\]}]+$/, ""));
  for (const m of t.matchAll(RE_PR)) push("pr", m[1] ?? m[2]);

  return out;
}

// Does `t` carry `addr` (the address the doer actually reported)?
function carries(t, addr) {
  const found = addressesIn(t);
  if (addr.kind === "sha") {
    return found.some(
      (f) =>
        f.kind === "sha" &&
        ((f.value.length >= 7 && addr.value.startsWith(f.value)) ||
          (addr.value.length >= 7 && f.value.startsWith(addr.value))),
    );
  }
  if (addr.kind === "uri") {
    // tolerate a trailing-slash / .git difference between report and dispatch
    const norm = (u) => u.replace(/\.git$/, "").replace(/\/+$/, "").toLowerCase();
    return found.some((f) => f.kind === "uri" && norm(f.value) === norm(addr.value));
  }
  if (addr.kind === "branch") {
    // `refs/heads/slice/x`, `origin/slice/x` and `branch slice/x` all name ONE branch.
    // Comparing them literally fails a run purely because the report and the dispatch
    // chose different (equivalent) spellings, which is not a handoff defect.
    const norm = (b) => b.replace(/^refs\/(?:heads|tags)\//, "").replace(/^origin\//, "").toLowerCase();
    return found.some((f) => f.kind === "branch" && norm(f.value) === norm(addr.value));
  }
  return found.some((f) => f.kind === addr.kind && f.value === addr.value);
}

// ---------- instruction recognition (doer prompt) ----------

const instructsMaterialize = (raw) =>
  /\b(commit(?:s|ted|ting)?|push(?:es|ed|ing)?|publish(?:es|ed|ing)?|materiali[sz]e[sd]?|persist(?:s|ed|ing)?)\b/i.test(
    flat(raw),
  );

const instructsReportAddress = (raw) =>
  /\b(report|include|state|provide|return|give|tell|record|hand back|hand over)\b.{0,120}?\b(address|sha|hash|commit|branch|url|uri|pr\b|pull request)\b/i.test(
    flat(raw),
  ) ||
  /\b(address|sha|hash|commit id|branch name)\b.{0,100}?\b(in|with|as part of|alongside|back)\b.{0,60}?\b(report|status|summary|reply|response)\b/i.test(
    flat(raw),
  );

// ---------- budget recognition ----------
//
// Two signals must both be present somewhere in the dispatch (doer prompt, any verifier
// prompt, or an optional ledger file):
//   round index      "round 2", "round 2/3", "round 2 of 3", "panel round: 2",
//                    and the synonyms models reach for: "iteration 2", "cycle 2",
//                    "panel pass 2", "2nd round"
//   remaining budget "budget 4 remaining", "remaining budget: 4", "3 dispatches left",
//                    "doer budget 5", "budget: 4/5"
//
// Invariant 6 names TWO counters — panel rounds and the doer-dispatch budget — and says
// every dispatch states both. So "round 2/3" alone does NOT satisfy the budget signal: it
// gives the round index and the round cap, while saying nothing about the doer budget.
// Letting the combined form set both flags was a false-PASS vector (caught in review): a
// dispatch that never mentions the doer budget would have scored as carrying it.

const ROUND = "(?:round|iteration|cycle|panel pass|pass)";
const RE_ROUND_COMBINED = new RegExp(`\\b${ROUND}\\b.{0,20}?\\b(\\d+)\\s*(?:\\/|of|out of)\\s*(\\d+)\\b`, "i");
const RE_ROUND_INDEX = new RegExp(
  `\\b${ROUND}\\b.{0,20}?[#:]?\\s*(\\d+)\\b|\\b(\\d+)(?:st|nd|rd|th)\\s+(?:panel\\s+)?${ROUND}\\b`,
  "i",
);
const RE_BUDGET =
  /\bbudget\b.{0,40}?\d+|\d+.{0,40}?\bbudget\b|\b\d+\s+(?:doer\s+)?(?:dispatch(?:es)?|round(?:s)?|iteration(?:s)?|attempt(?:s)?)\s+(?:remaining|left)\b|\b(?:remaining|left)\b.{0,30}?\b\d+\b/i;

function budgetSignals(rawTexts) {
  let round = false;
  let budget = false;
  for (const t of rawTexts.map(flat)) {
    if (RE_ROUND_COMBINED.test(t)) round = true;
    if (RE_ROUND_INDEX.test(t)) round = true;
    if (RE_BUDGET.test(t)) budget = true;
  }
  return { round, budget };
}

// ---------- load the capture ----------

let files;
try {
  files = readdirSync(dir);
} catch (e) {
  console.error(`cannot read dispatched dir: ${dir} (${e.message})`);
  process.exit(2);
}

const read = (f) => readFileSync(join(dir, f), "utf8");

const doerFile = files.find((f) => /^doer\.txt$/i.test(f));
const reportFile = files.find((f) => /^doer[-_]report\.txt$/i.test(f));
const verifierFiles = files.filter((f) => /^verifier[-_].*\.txt$/i.test(f)).sort();
const ledgerFiles = files.filter((f) => /^ledger\.(txt|md)$/i.test(f));

const failures = [];
const note = (cond, msg) => {
  if (!cond) failures.push(msg);
  return cond;
};

// --- 1. the doer prompt asks for durable materialization + an address ---
let materializeOk = false;
if (note(!!doerFile, "no doer.txt found in dispatched dir")) {
  const t = read(doerFile);
  const m = note(
    instructsMaterialize(t),
    "doer prompt does not instruct durable materialization (commit / push / publish)",
  );
  const r = note(
    instructsReportAddress(t),
    "doer prompt does not instruct reporting the artifact's fetchable address",
  );
  materializeOk = m && r;
}

// --- 2. the doer report contains a fetchable address ---
let reported = [];
let addressOk = false;
if (note(!!reportFile, "no doer-report.txt found in dispatched dir")) {
  const t = read(reportFile);
  reported = addressesIn(t);
  addressOk = note(
    reported.length > 0,
    "doer report contains no fetchable address (commit / branch / URI / PR) — only working-tree paths",
  );
}

// --- 3. every verifier prompt carries that address ---
note(verifierFiles.length >= 1, "no verifier-*.txt files found in dispatched dir");
let verifiersOk = verifierFiles.length >= 1;
for (const f of verifierFiles) {
  const t = read(f);
  const id = basename(f);
  let ok;
  if (reported.length > 0) {
    ok = note(
      reported.some((a) => carries(t, a)),
      `${id}: does not carry the artifact address the doer reported (${reported
        .map((a) => `${a.kind}:${a.value}`)
        .join(", ")})`,
    );
  } else {
    // no address to cross-match against; fall back to "carries some address at all"
    ok = note(
      addressesIn(t).length > 0,
      `${id}: carries no fetchable address, only working-tree paths`,
    );
  }
  verifiersOk = verifiersOk && ok;
}

// --- 4. round index + remaining budget travel in the dispatch or ledger ---
const budgetTexts = [
  ...(doerFile ? [read(doerFile)] : []),
  ...verifierFiles.map(read),
  ...ledgerFiles.map(read),
];
const sig = budgetSignals(budgetTexts);
const r = note(sig.round, "no panel-round index in the dispatch or ledger");
const b = note(
  sig.budget,
  "no remaining doer-dispatch budget in the dispatch or ledger (a round cap like \"round 1/3\" is not the doer budget)",
);
const budgetOk = r && b;

// ---------- report ----------

const pass = failures.length === 0;

console.log(`METRIC doer_materialize_instruction=${materializeOk ? 1 : 0}`);
console.log(`METRIC artifact_address_reported=${addressOk ? 1 : 0}`);
console.log(`METRIC verifier_count=${verifierFiles.length}`);
console.log(`METRIC verifier_address_propagation=${verifiersOk ? 1 : 0}`);
console.log(`METRIC budget_carried=${budgetOk ? 1 : 0}`);
console.log(`METRIC handoff_durability=${pass ? 1 : 0}`);

if (pass) {
  const shown = reported.map((a) => `${a.kind}:${a.value}`).join(", ");
  console.log(
    `\nPASS — artifact materialized and handed over by address (${shown}) to ${verifierFiles.length} verifier(s); budget carried.`,
  );
  process.exit(0);
} else {
  console.log(`\nFAIL (${failures.length}):`);
  for (const m of failures) console.log(`  - ${m}`);
  process.exit(1);
}
