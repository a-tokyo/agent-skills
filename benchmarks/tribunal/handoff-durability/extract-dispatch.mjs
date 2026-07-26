#!/usr/bin/env node
// extract-dispatch.mjs <transcript.jsonl> <out-dir>
//
// Recovers the prompts the orchestrator actually dispatched, out of a Claude Code
// `--output-format stream-json --verbose` transcript, into the layout check.mjs expects:
//
//   <out-dir>/doer.txt           the first doer/builder Task prompt
//   <out-dir>/doer-report.txt    that Task's tool_result (what the doer handed back)
//   <out-dir>/verifier-<lens>.txt one per verifier Task prompt
//
// propagation-fidelity/ asks the agent to save its own prompts, which tells the agent its
// dispatch is being inspected. Reading them out of the transcript instead keeps the agent
// blind to the measurement — worth the parsing.
//
// DESIGN NOTE — schema drift. The exact stream-json envelope is not a stable public
// contract, so this walks the JSON recursively for anything shaped like a `tool_use` /
// `tool_result` block rather than indexing fixed paths. If the shape changes, extraction
// degrades to "found nothing" (exit 3) rather than silently emitting empty files. The
// harness keeps the raw transcript, so extraction can be fixed and re-run offline without
// re-spending a capture.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const [transcript, outDir] = process.argv.slice(2);
if (!transcript || !outDir) {
  console.error("usage: node extract-dispatch.mjs <transcript.jsonl> <out-dir>");
  process.exit(2);
}

// ---------- parse ----------

const lines = readFileSync(transcript, "utf8").split("\n");
const events = [];
for (const line of lines) {
  const s = line.trim();
  if (!s || s[0] !== "{") continue;
  try {
    events.push(JSON.parse(s));
  } catch {
    // a truncated final line is normal when a run is killed by the timeout
  }
}
if (!events.length) {
  console.error(`no JSON events in ${transcript} — was the run started with --output-format stream-json --verbose?`);
  process.exit(3);
}

const toolUses = []; // { id, name, input }
const toolResults = new Map(); // tool_use_id -> text
const subagentText = new Map(); // parent_tool_use_id -> [text, …] streamed BY the subagent

// Subagent dispatch has two shapes on the wire, and only one of them puts the report in
// the tool_result:
//
//   synchronous  — the Task/Agent tool_result IS the subagent's report.
//   asynchronous — the tool_result is a LAUNCH ACKNOWLEDGEMENT ("Async agent launched
//                  successfully… you will be notified"), and the subagent's real output
//                  arrives later as assistant events carrying `parent_tool_use_id`.
//
// Verified against a live `--output-format stream-json --verbose` capture. Treating the
// async stub as the report is the dangerous case: it is a perfectly well-formed file
// containing no address, so `artifact_address_reported` would read 0 on every run of BOTH
// arms — a confident, uniform, and completely wrong number. Hence the explicit detection.
const isLaunchStub = (t) =>
  /async agent launched|agent is working in the background|you will be notified automatically/i.test(
    t,
  );

function textOf(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((c) => (typeof c === "string" ? c : typeof c?.text === "string" ? c.text : ""))
      .filter(Boolean)
      .join("\n");
  }
  if (content && typeof content.text === "string") return content.text;
  return "";
}

function walk(node) {
  if (Array.isArray(node)) return node.forEach(walk);
  if (!node || typeof node !== "object") return;

  if (node.type === "tool_use" && node.name) {
    toolUses.push({ id: node.id, name: node.name, input: node.input ?? {} });
  } else if (node.type === "tool_result" && node.tool_use_id) {
    const t = textOf(node.content);
    if (t) toolResults.set(node.tool_use_id, t);
  }

  for (const v of Object.values(node)) walk(v);
}
events.forEach(walk);

// Second pass at EVENT level: `parent_tool_use_id` lives on the event envelope, not on the
// content blocks, so the recursive walk above cannot see which dispatch a message belongs to.
for (const e of events) {
  const parent = e?.parent_tool_use_id;
  if (!parent || e.type !== "assistant") continue;
  for (const c of e.message?.content ?? []) {
    if (c?.type === "text" && typeof c.text === "string" && c.text.trim()) {
      if (!subagentText.has(parent)) subagentText.set(parent, []);
      subagentText.get(parent).push(c.text);
    }
  }
}

// The report for a dispatch: its tool_result when that is genuinely the report, otherwise
// everything the subagent itself said under that dispatch.
function reportFor(id) {
  const direct = toolResults.get(id);
  if (direct && !isLaunchStub(direct)) return direct;
  const streamed = subagentText.get(id);
  if (streamed?.length) return streamed.join("\n\n");
  return null;
}

// ---------- select the subagent dispatches ----------

const isSubagentTool = (n) => /^(Task|Agent|Dispatch)$/i.test(n);
const dispatches = toolUses.filter((t) => isSubagentTool(t.name));

if (!dispatches.length) {
  console.error(
    `no subagent dispatches (Task/Agent tool_use) found in ${transcript} — the orchestrator ` +
      `never convened a doer or a panel. That is a real result, not an extraction bug: ` +
      `inspect the transcript before scoring it.`,
  );
  process.exit(3);
}

// Every field a dispatch might put the prompt in, most specific first.
const promptOf = (input) =>
  [input.prompt, input.message, input.instructions, input.task, input.input]
    .find((v) => typeof v === "string" && v.trim()) ?? JSON.stringify(input, null, 2);

const labelOf = (input) =>
  [input.description, input.subagent_type, input.agent_type, input.name]
    .filter((v) => typeof v === "string")
    .join(" ");

// Role classification. Keyword presence alone does NOT work: a v0.0.3 doer prompt talks
// about the verifiers on purpose ("the verifiers run in separate sessions and cannot see
// your working tree"), so any rule of the form "mentions 'verifier' => is a verifier"
// files the doer under the panel and the capture loses doer.txt entirely.
//
// Instead, weigh the role vocabulary the skill actually assigns to each role. The two
// status enums are near-perfect discriminators — only a doer is told to answer
// DONE_WITH_CONCERNS / NEEDS_CONTEXT, and only a verifier is told to recommend
// SHIP_WITH_CAVEATS — so they carry the most weight, with the dispatch label next.
const DOER_SIGNALS = [
  [/\b(?:you are the doer|role:\s*doer)\b/i, 4],
  [/\bDONE_WITH_CONCERNS\b|\bNEEDS_CONTEXT\b/, 4],
  [/\bimplement (?:exactly )?(?:this|the) slice\b/i, 3],
  [/\b(?:you are the (?:builder|implementer))\b/i, 3],
];
const VERIFIER_SIGNALS = [
  [/\brole:\s*[\w-]+\s+(?:verifier|reviewer)\b/i, 4],
  [/\bSHIP_WITH_CAVEATS\b/, 4],
  [/\byou must oppose\b/i, 4],
  [/\bscore each dimension\b/i, 3],
  [/\byou have not seen the (?:builder|doer)'?s reasoning\b/i, 3],
];
const RE_LABEL_DOER = /\b(?:doer|builder|implement\w*)\b/i;
const RE_LABEL_VERIFIER = /\b(?:verif\w*|review\w*|adversar\w*|critic|lens|panel|quality|fitness|audit\w*)\b/i;

const score = (signals, text) => signals.reduce((n, [re, w]) => n + (re.test(text) ? w : 0), 0);

const slug = (s) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40) || "unnamed";

// Lens name, preferring an explicit "<lens> verifier" over the whole description.
function lensOf(input, fallbackIndex) {
  const label = labelOf(input);
  const m =
    label.match(/\b([a-z][\w-]*)[\s-]+(?:verifier|reviewer|lens)\b/i) ||
    label.match(/\b(adversary|adversarial|quality|fitness[\w-]*|correctness|security|operator)\b/i);
  if (m) return slug(m[1]);
  if (label.trim()) return slug(label);
  return `${fallbackIndex}`;
}

mkdirSync(outDir, { recursive: true });

let doerSeen = 0;
let verifierSeen = 0;
const written = [];
const write = (name, body) => {
  writeFileSync(join(outDir, name), body.endsWith("\n") ? body : body + "\n");
  written.push(name);
};

for (const [i, d] of dispatches.entries()) {
  const label = labelOf(d.input);
  const body = promptOf(d.input);
  const doerScore = score(DOER_SIGNALS, body) + (RE_LABEL_DOER.test(label) ? 3 : 0);
  const verifierScore = score(VERIFIER_SIGNALS, body) + (RE_LABEL_VERIFIER.test(label) ? 3 : 0);
  // On a genuine tie, the loop's own ordering decides: the doer finishes before the panel
  // is convened, so the first dispatch of a run is the doer.
  const isDoer =
    doerScore > verifierScore || (doerScore === verifierScore && i === 0 && doerSeen === 0);

  if (isDoer) {
    doerSeen++;
    write(doerSeen === 1 ? "doer.txt" : `doer-${doerSeen}.txt`, promptOf(d.input));
    const report = reportFor(d.id);
    if (report) write(doerSeen === 1 ? "doer-report.txt" : `doer-report-${doerSeen}.txt`, report);
  } else {
    verifierSeen++;
    write(`verifier-${lensOf(d.input, verifierSeen)}.txt`, promptOf(d.input));
  }
}

console.error(`extracted ${written.length} file(s) into ${outDir}: ${written.join(", ")}`);

// A missing doer-report.txt is an EXTRACTION failure, and must never reach check.mjs — the
// checker would report "no doer-report.txt found", which reads exactly like the skill having
// failed to elicit an address. Scoring a parser bug as a skill regression is the specific way
// this eval could produce a confidently wrong number, so it exits loudly instead.
//
// Deliberately NOT recovered from the run's final `result` text: that is the orchestrator's
// closing summary, not the doer's report, and substituting it would corrupt
// artifact_address_reported.
if (written.includes("doer.txt") && !written.includes("doer-report.txt")) {
  console.error(
    `\nERROR: no report recoverable for the doer dispatch (no usable tool_result, and no\n` +
      `subagent output under its parent_tool_use_id), so doer-report.txt is missing.\n` +
      `This capture is NOT scorable. The raw transcript is retained — fix the parser and\n` +
      `re-run this extractor offline rather than re-spending the run.`,
  );
  process.exit(4);
}
