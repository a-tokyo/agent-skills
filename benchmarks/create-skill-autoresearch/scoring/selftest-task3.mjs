#!/usr/bin/env node
// selftest-task3.mjs — offline proof that the task-3 probes discriminate. Must print SELFTEST PASS.
// Runs the reference solver (good fixture) and the stale-guide solver (bad fixture) on every holdout with
// python3, no executor and no API: perfect output must score 1.0, the stale guide must score low.
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { checkQuote, checkCompliance, checkCraft, WEIGHTS } from "./score-task3.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const TASK = join(HERE, "..", "task-3");
const key = JSON.parse(readFileSync(join(TASK, "holdout", "answer-key.json"), "utf8"));
const failures = [];
const expect = (name, cond) => { if (!cond) failures.push(name); };

for (const [id, c] of Object.entries(key)) {
  expect(`${id} perfect = 1`, checkQuote(JSON.stringify(c.expected), c.expected).score === 1);
  const wrongCarrier = { ...c.expected, carrier_id: "X99" };
  const r = checkQuote(wrongCarrier, c.expected);
  expect(`${id} wrong carrier loses exactly one check`, r.passed === r.total - 1);
  const nudged = { ...c.expected, total_eur: c.expected.total_eur + 0.05 };
  expect(`${id} total off by 0.05 fails total`, !checkQuote(nudged, c.expected).checks.total);
  const shuffled = { ...c.expected, surcharges: [...c.expected.surcharges].reverse() };
  expect(`${id} surcharge order is not scored`, checkQuote(shuffled, c.expected).score === 1);
  if (c.expected.surcharges.length) {
    const off = structuredClone(c.expected);
    off.surcharges[0].amount_eur += 1;
    const o = checkQuote(off, c.expected);
    expect(`${id} wrong surcharge amount fails only surcharge_amounts`, !o.checks.surcharge_amounts && o.passed === o.total - 1);
  }
  if (c.expected.exceptions.length) {
    const wrongDetail = structuredClone(c.expected);
    wrongDetail.exceptions[0].detail = "something else";
    const d = checkQuote(wrongDetail, c.expected);
    expect(`${id} wrong exception detail fails only exception_details`, !d.checks.exception_details && d.checks.exception_codes);
  }
  expect(`${id} garbage = 0`, checkQuote("not json", c.expected).score === 0);
  expect(`${id} empty file = 0`, checkQuote("", c.expected).score === 0);
}

const good = join(HERE, "fixtures", "t3-good-skill");
const bad = join(HERE, "fixtures", "t3-bad-skill");
expect("good fixture script is the reference solver",
  readFileSync(join(good, "scripts", "quote_tools.py"), "utf8") === readFileSync(join(TASK, "tools", "solve.py"), "utf8"));
expect("good compliance = 1", checkCompliance(good).score === 1);
expect("good craft = 1", checkCraft(good).score === 1);

// Offline execution: run each fixture's documented command on every holdout and score its stdout.
const command = (skill) => {
  const md = readFileSync(join(skill, "SKILL.md"), "utf8");
  const stale = md.match(/--stale (\S+)/)?.[1];
  return stale ? ["--stale", stale] : [];
};
const offline = (skill) => {
  const scores = Object.entries(key).map(([, c]) => {
    const out = spawnSync("python3", ["-B", join(skill, "scripts", "quote_tools.py"), join(TASK, "holdout", "rates"),
      join(TASK, "holdout", c.input), ...command(skill)], { encoding: "utf8" });
    return checkQuote(out.stdout, c.expected).score;
  });
  return { scores, mean: scores.reduce((a, b) => a + b, 0) / scores.length };
};
const g = offline(good);
const b = offline(bad);
const overall = (skill, exec) => WEIGHTS.execution * exec + WEIGHTS.compliance * checkCompliance(skill).score
  + WEIGHTS.craft * checkCraft(skill).score;
console.log(`# good offline execution=${g.mean.toFixed(3)} overall=${overall(good, g.mean).toFixed(3)} per-case=[${g.scores.map((s) => s.toFixed(2))}]`);
console.log(`# bad  offline execution=${b.mean.toFixed(3)} overall=${overall(bad, b.mean).toFixed(3)} per-case=[${b.scores.map((s) => s.toFixed(2))}]`);
expect("good fixture scores 1.0 on every holdout", g.scores.every((s) => s === 1));
expect("stale-guide fixture execution <= 0.45", b.mean <= 0.45);
expect("stale-guide fixture overall <= 0.55", overall(bad, b.mean) <= 0.55);

if (failures.length) {
  console.log(`SELFTEST FAIL\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("SELFTEST PASS");
