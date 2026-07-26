#!/usr/bin/env node
// Offline self-test for check.mjs — no API, no network. Confirms the checker PASSes a
// durable handoff and FAILs each of the three seeded regressions, and — the part that
// matters — that each failing fixture fails for the RIGHT reason: exactly the metric it
// was seeded to break, with the others still green.
//
// A checker that fails every bad fixture wholesale would pass a naive exit-code test
// while being useless at diagnosing a live run, so the per-metric expectations below
// are the real assertion.
//
// Run: node selftest.mjs   (exit 0 = the checker itself is sound)

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { readFileSync, writeFileSync, mkdtempSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";

const here = dirname(fileURLToPath(import.meta.url));
const check = join(here, "check.mjs");
const extract = join(here, "extract-dispatch.mjs");

// `metrics` lists only the metrics whose value is asserted; unlisted ones are ignored.
const cases = [
  {
    dir: "fixtures/pass",
    expect: 0,
    why: "artifact committed, address propagated to both verifiers, budget carried",
    metrics: {
      doer_materialize_instruction: 1,
      artifact_address_reported: 1,
      verifier_address_propagation: 1,
      budget_carried: 1,
      handoff_durability: 1,
    },
  },
  {
    // Regression guard (found in review): the report and the dispatches spell the same
    // address differently — backticked URI vs bare, refs/heads/x vs origin/x. Equivalent
    // spellings must not read as a broken handoff. The adversary prompt carries ONLY the
    // origin/ branch form, so branch normalization is genuinely exercised, not masked by
    // the SHA also matching.
    dir: "fixtures/pass-address-forms",
    expect: 0,
    why: "equivalent address spellings across report and dispatch still count as the same address",
    metrics: {
      artifact_address_reported: 1,
      verifier_address_propagation: 1,
      budget_carried: 1,
      handoff_durability: 1,
    },
  },
  {
    dir: "fixtures/fail-no-address",
    expect: 1,
    why: "pre-v0.0.3 dispatch: no materialize instruction, so the report gives only paths",
    metrics: {
      doer_materialize_instruction: 0,
      artifact_address_reported: 0,
      verifier_address_propagation: 0,
      budget_carried: 1,
      handoff_durability: 0,
    },
  },
  {
    dir: "fixtures/fail-ephemeral-path",
    expect: 1,
    why: "doer committed, but the panel was pointed at the doer's working tree instead",
    metrics: {
      doer_materialize_instruction: 1,
      artifact_address_reported: 1,
      verifier_address_propagation: 0,
      budget_carried: 1,
      handoff_durability: 0,
    },
  },
  {
    dir: "fixtures/fail-budget-not-carried",
    expect: 1,
    why: "durable handoff, but the round index and budget live only in orchestrator context",
    metrics: {
      doer_materialize_instruction: 1,
      artifact_address_reported: 1,
      verifier_address_propagation: 1,
      budget_carried: 0,
      handoff_durability: 0,
    },
  },
];

const parseMetrics = (out) =>
  Object.fromEntries(
    [...out.matchAll(/^METRIC (\w+)=(-?\d+)$/gm)].map(([, k, v]) => [k, Number(v)]),
  );

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

  const problems = [];
  if (code !== c.expect) problems.push(`exit ${code} (expected ${c.expect})`);
  const got = parseMetrics(out);
  for (const [k, want] of Object.entries(c.metrics ?? {})) {
    if (got[k] !== want) problems.push(`${k}=${got[k] ?? "<missing>"} (expected ${want})`);
  }

  const ok = problems.length === 0;
  if (!ok) bad++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${c.dir} — ${c.why}`);
  if (!ok) {
    for (const p of problems) console.log(`        ${p}`);
    console.log(out.replace(/^/gm, "        "));
  }
}

// ---------------------------------------------------------------------------
// Extractor plumbing: fixtures -> synthetic stream-json -> extract-dispatch.mjs -> check.mjs
//
// The fixtures alone only prove the checker reads a directory correctly. On a live run that
// directory is built by extract-dispatch.mjs, and a bug there costs a paid capture — it
// already ate one during development, by filing the doer prompt under the panel because a
// v0.0.3 doer prompt legitimately mentions the verifiers. This replays the whole chain
// offline. The envelope below was verified against a live `--output-format stream-json
// --verbose` capture — `tool_use` blocks carry id/name/input, and `tool_result.content` may
// be a bare string or an array of typed blocks.
// ---------------------------------------------------------------------------

function synthStream(fixtureDir) {
  const f = (n) => readFileSync(join(here, fixtureDir, n), "utf8");
  const tu = (id, description, prompt) => ({
    type: "assistant",
    message: {
      content: [
        { type: "tool_use", id, name: "Task", input: { description, subagent_type: "general-purpose", prompt } },
      ],
    },
  });
  const tr = (id, text) => ({
    type: "user",
    message: { content: [{ type: "tool_result", tool_use_id: id, content: [{ type: "text", text }] }] },
  });
  return [
    { type: "system", subtype: "init", session_id: "selftest" },
    tu("t1", "doer: implement the parseRetryAfter slice", f("doer.txt")),
    tr("t1", f("doer-report.txt")),
    tu("t2", "quality verifier", f("verifier-quality.txt")),
    tu("t3", "adversary verifier", f("verifier-adversary.txt")),
    tr("t2", "scores omitted"),
    tr("t3", "scores omitted"),
    { type: "result", subtype: "success", result: "Verdict: SHIP" },
  ]
    .map((o) => JSON.stringify(o))
    .join("\n");
}

// The ASYNC dispatch envelope, transcribed from a live capture. Here the doer's tool_result
// is only a launch acknowledgement and the real report arrives as assistant events tagged
// with `parent_tool_use_id`. Mistaking the stub for the report yields a well-formed
// doer-report.txt containing no address — which would score artifact_address_reported=0 on
// every run of BOTH arms, a uniform and confidently wrong result. This case exists so that
// can never regress silently.
function synthAsyncStream(fixtureDir) {
  const f = (n) => readFileSync(join(here, fixtureDir, n), "utf8");
  const tu = (id, description, prompt) => ({
    type: "assistant",
    message: {
      content: [
        { type: "tool_use", id, name: "Agent", input: { description, subagent_type: "general-purpose", prompt } },
      ],
    },
  });
  const tr = (id, text) => ({
    type: "user",
    message: { content: [{ type: "tool_result", tool_use_id: id, content: [{ type: "text", text }] }] },
  });
  const subagentSays = (parent, text) => ({
    type: "assistant",
    parent_tool_use_id: parent,
    subagent_type: "general-purpose",
    message: { content: [{ type: "text", text }] },
  });
  return [
    { type: "system", subtype: "init", session_id: "selftest" },
    tu("t1", "doer: implement the parseRetryAfter slice", f("doer.txt")),
    { type: "system", subtype: "task_started", task_id: "k1", tool_use_id: "t1" },
    tr("t1", "Async agent launched successfully. The agent is working in the background. You will be notified automatically when it completes."),
    subagentSays("t1", f("doer-report.txt")),
    { type: "system", subtype: "task_notification", task_id: "k1", tool_use_id: "t1", status: "completed" },
    tu("t2", "quality verifier", f("verifier-quality.txt")),
    tu("t3", "adversary verifier", f("verifier-adversary.txt")),
    tr("t2", "scores omitted"),
    tr("t3", "scores omitted"),
    { type: "result", subtype: "success", result: "Verdict: SHIP" },
  ]
    .map((o) => JSON.stringify(o))
    .join("\n");
}

const plumbing = [
  { fixture: "fixtures/pass", expect: 0, why: "a durable-handoff run survives extraction and PASSes" },
  { fixture: "fixtures/fail-no-address", expect: 1, why: "a path-only run survives extraction and still FAILs" },
  {
    fixture: "fixtures/pass",
    async: true,
    expect: 0,
    why: "async dispatch: the report is recovered from the subagent's own output, not the launch stub",
  },
];

for (const p of plumbing) {
  const tmp = mkdtempSync(join(tmpdir(), "handoff-selftest-"));
  const stream = join(tmp, "stream.jsonl");
  const out = join(tmp, "dispatched");
  writeFileSync(stream, (p.async ? synthAsyncStream(p.fixture) : synthStream(p.fixture)) + "\n");

  const problems = [];
  try {
    execFileSync("node", [extract, stream, out], { encoding: "utf8", stdio: "pipe" });
  } catch (e) {
    problems.push(`extract-dispatch.mjs exited ${e.status}: ${(e.stderr || "").trim()}`);
  }

  if (!problems.length) {
    const got = readdirSync(out).sort();
    for (const need of ["doer.txt", "doer-report.txt", "verifier-quality.txt", "verifier-adversary.txt"]) {
      if (!got.includes(need)) problems.push(`extraction did not produce ${need} (got: ${got.join(", ")})`);
    }
  }

  if (!problems.length) {
    let code = 0;
    try {
      execFileSync("node", [check, out], { encoding: "utf8" });
    } catch (e) {
      code = e.status ?? 1;
    }
    if (code !== p.expect) problems.push(`check.mjs exit ${code} (expected ${p.expect})`);
  }

  const ok = problems.length === 0;
  if (!ok) bad++;
  console.log(`${ok ? "ok  " : "FAIL"}  extract+check ${p.fixture}${p.async ? " (async)" : ""} — ${p.why}`);
  for (const m of problems) console.log(`        ${m}`);
}

// ---------------------------------------------------------------------------
// Address recognition: "branch <word>" is also ordinary English.
//
// The branch form is the loosest of the four address kinds, and it errs in BOTH directions:
// too strict and a real `branch feature-x` handoff reads as path-only; too loose and a
// v0.0.2 report that merely says "the branch we discussed" scores as having reported an
// address — which would erase the very difference the arms exist to measure. Both
// directions are pinned here.
// ---------------------------------------------------------------------------

const recognition = [
  ["branch slice/retry-after", 1, "ref with a slash"],
  ["branch feature-x", 1, "ref with a hyphen"],
  ["branch main", 1, "conventional branch name"],
  ["tag v1.2.3", 1, "tag with digits"],
  ["the branch we discussed", 0, "prose, not a ref"],
  // file:// is a local path wearing a scheme — the ephemeral-artifact case itself.
  ["the artifact at file:///tmp/tribunal-run/work", 0, "file:// is not a fetchable address"],
  ["https://github.com/acme/repo/commit/abc1234", 1, "a remote URI is"],
  ["review the branch a doer created", 0, "prose, not a ref"],
  ["work is on the branch", 0, "prose ending a sentence"],
];

for (const [phrase, want, why] of recognition) {
  const d = mkdtempSync(join(tmpdir(), "handoff-selftest-addr-"));
  writeFileSync(join(d, "doer.txt"), "Commit it and report the address.\nRound 1/3, doer budget 4 of 5 remaining.\n");
  writeFileSync(join(d, "doer-report.txt"), `DONE. Work is on ${phrase}.\n`);
  writeFileSync(
    join(d, "verifier-quality.txt"),
    `Role: quality verifier. Artifact: ${phrase}. Round 1/3, doer budget 4 of 5 remaining.\n`,
  );

  let out = "";
  try {
    out = execFileSync("node", [check, d], { encoding: "utf8" });
  } catch (e) {
    out = (e.stdout || "") + (e.stderr || "");
  }
  const got = parseMetrics(out).artifact_address_reported;
  const ok = got === want;
  if (!ok) bad++;
  console.log(
    `${ok ? "ok  " : "FAIL"}  address "${phrase}" -> reported=${got} (expected ${want}) — ${why}`,
  );
}

// ---------------------------------------------------------------------------
// Budget phrasing, transcribed verbatim from a live v0.0.3 capture.
//
// The first draft of RE_BUDGET failed ALL of these and passed the run as
// budget_carried=0 while the orchestrator was in fact tracking spend meticulously in its
// ledger — a false FAIL on the metric most likely to be mistaken for a skill regression.
// The lesson: an orchestrator tracking spend writes "#2 of 5", not "3 remaining".
// ---------------------------------------------------------------------------

const budgetPhrases = [
  ["Budgets: max 3 panel rounds, max 5 doer dispatches", 1, "ledger header, plural 'Budgets'"],
  ["Budgets used: 3 of 3 panel rounds, 3 of 5 doer dispatches", 1, "ledger footer"],
  ["doer dispatch #2 of a 5-dispatch budget; panel round 1 of 3 already ran", 1, "re-dispatch preamble"],
  ["doer dispatch #1 of 5 used", 1, "ledger round entry"],
  ["Doer dispatch budget: 4 of 5 remaining.", 1, "the fixture phrasing"],
  ["", 0, "a round index alone is not the doer budget"],
  ["Score each dimension 1-10 with confidence 0.0-1.0.", 0, "scoring scale is not a budget"],
];

// The round index is held CONSTANT across these so budget_carried isolates the budget half
// (it is the AND of both signals). Without that, a case would fail for the wrong reason.
for (const [phrase, want, why] of budgetPhrases) {
  const d = mkdtempSync(join(tmpdir(), "handoff-selftest-budget-"));
  writeFileSync(join(d, "doer.txt"), `Commit it and report the commit SHA.\nPanel round 1 of 3.\n${phrase}\n`);
  writeFileSync(join(d, "doer-report.txt"), "DONE. Committed as 4f2c1a9e0b7d3c8f5e6a2b4c9d0e1f3a5b7c8d92.\n");
  writeFileSync(join(d, "verifier-quality.txt"), `Role: quality verifier. Artifact: commit 4f2c1a9. Panel round 1 of 3. ${phrase}\n`);

  let out = "";
  try {
    out = execFileSync("node", [check, d], { encoding: "utf8" });
  } catch (e) {
    out = (e.stdout || "") + (e.stderr || "");
  }
  const got = parseMetrics(out).budget_carried;
  const ok = got === want;
  if (!ok) bad++;
  console.log(`${ok ? "ok  " : "FAIL"}  budget "${(phrase || "(round index only)").slice(0, 46)}…" -> ${got} (expected ${want}) — ${why}`);
}

const total = cases.length + plumbing.length + recognition.length + budgetPhrases.length;
if (bad) {
  console.log(`\n${bad} self-test case(s) failed — the checker is not sound.`);
  process.exit(1);
}
console.log(`\nAll ${total} self-test cases passed — check.mjs and extract-dispatch.mjs are sound.`);
