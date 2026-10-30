#!/usr/bin/env node
// selftest-task3.mjs — offline proof that the task-3 probes discriminate. Must print SELFTEST PASS.
// Runs the reference solver (good fixture), the stale-guide solver (bad fixture) and a gold-fitter on every
// holdout with python3, no executor and no API: perfect output must score 1.0, the other two must score low.
import { readFileSync, readdirSync } from "node:fs";
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
const staleOf = (skill) => readFileSync(join(skill, "SKILL.md"), "utf8").match(/--stale (\S+)/)?.[1];
const offline = (skill, stale = staleOf(skill)) => {
  const scores = Object.entries(key).map(([, c]) => {
    const out = spawnSync("python3", ["-B", join(skill, "scripts", "quote_tools.py"), join(TASK, "holdout", "rates"),
      join(TASK, "holdout", c.input), ...(stale ? ["--stale", stale] : [])], { encoding: "utf8" });
    return checkQuote(out.stdout, c.expected).score;
  });
  return { scores, mean: scores.reduce((a, b) => a + b, 0) / scores.length };
};
const overall = (skill, exec) => WEIGHTS.execution * exec + WEIGHTS.compliance * checkCompliance(skill).score
  + WEIGHTS.craft * checkCraft(skill).score;
const show = (label, skill, r) =>
  console.log(`# ${label} offline execution=${r.mean.toFixed(3)} overall=${overall(skill, r.mean).toFixed(3)} per-case=[${r.scores.map((s) => s.toFixed(2))}]`);
const g = offline(good);
const b = offline(bad);
// A gold-fitter: the reference solver with every class-C shortcut (training-table constants, surcharge
// unit by code, every holiday row, first zone row, no box-weight limit). It reproduces all eight gold quotes.
const FIT = "c_round,c_size,c_owt,unit_by_code,hol_any,zone_first,max_box";
const f = offline(good, FIT);
show("good       ", good, g);
show("stale-guide", bad, b);
show("gold-fitter", good, f);
expect("good fixture scores 1.0 on every holdout", g.scores.every((s) => s === 1));
expect("stale-guide fixture execution <= 0.50", b.mean <= 0.5);
expect("gold-fitter execution <= 0.55", f.mean <= 0.55);
const goldFits = readdirSync(join(TASK, "input")).filter((n) => /^shipment-\d+\.md$/.test(n)).every((n) => {
  const out = spawnSync("python3", ["-B", join(good, "scripts", "quote_tools.py"), join(TASK, "input", "rates"),
    join(TASK, "input", n), "--stale", FIT], { encoding: "utf8" });
  const want = JSON.parse(readFileSync(join(TASK, "input", n.replace("shipment", "quote").replace(".md", ".json")), "utf8"));
  return checkQuote(out.stdout, want).score === 1;
});
expect("gold-fitter reproduces every gold quote", goldFits);

if (failures.length) {
  console.log(`SELFTEST FAIL\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("SELFTEST PASS");
