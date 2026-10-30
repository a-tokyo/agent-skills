#!/usr/bin/env node
// selftest-task3.mjs — offline proof that the task-3 probes discriminate. Must print SELFTEST PASS.
// Runs the reference solver (good fixture), the prior-edition solver (bad fixture) and a "naive reader" (current
// tables, every clause in solve.NAIVE misread) on every holdout with python3, no executor and no API: perfect
// output must score 1.0, the other two must score low.
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { checkResult, checkCompliance, checkCraft, WEIGHTS, FIELDS } from "./score-task3.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const TASK = join(HERE, "..", "task-3");
const key = JSON.parse(readFileSync(join(TASK, "holdout", "answer-key.json"), "utf8"));
const failures = [];
const expect = (name, cond) => { if (!cond) failures.push(name); };

for (const [id, c] of Object.entries(key)) {
  const e = c.expected;
  expect(`${id} perfect = 1`, checkResult(JSON.stringify(e), e).score === 1);
  expect(`${id} garbage = 0`, checkResult("not json", e).score === 0);
  expect(`${id} empty file = 0`, checkResult("", e).score === 0);
  const off = { ...e, sai: (e.sai ?? 0) + 1 };
  const o = checkResult(off, e);
  expect(`${id} SAI off by one loses exactly the sai check`, !o.checks.sai && o.passed === o.total - 1);
  const str = Object.fromEntries(Object.entries(e).map(([k, v]) => [k, typeof v === "number" ? String(v) : v]));
  expect(`${id} numeric strings are accepted`, checkResult(str, e).score === 1);
  const nulls = Object.keys(e).filter((k) => e[k] === null);
  if (nulls.length) {
    const zeroed = { ...e, [nulls[0]]: 0 };
    expect(`${id} 0 for an expected null fails`, !checkResult(zeroed, e).checks[nulls[0]]);
    const { [nulls[0]]: _, ...missing } = e;
    expect(`${id} a missing key fails`, !checkResult(missing, e).checks[nulls[0]]);
  }
  expect(`${id} answer key holds every field`, FIELDS.every((f) => f in e));
}

const good = join(HERE, "fixtures", "t3-good-skill");
const bad = join(HERE, "fixtures", "t3-bad-skill");
const solver = readFileSync(join(TASK, "tools", "solve.py"), "utf8");
for (const s of [good, bad]) {
  expect(`${s} script is the reference solver`, readFileSync(join(s, "scripts", "sai_tools.py"), "utf8") === solver);
  expect(`${s} compliance = 1`, checkCompliance(s).score === 1);
  expect(`${s} craft = 1`, checkCraft(s).score === 1);
}

// Offline execution: run each fixture's documented command on every holdout and score its stdout.
const flagsOf = (skill) => readFileSync(join(skill, "SKILL.md"), "utf8").match(/applicant\.md((?: --\S+ \S+)*) >/)[1]
  .trim().split(/\s+/).filter(Boolean);
const offline = (skill, flags = flagsOf(skill)) => {
  const scores = Object.values(key).map((c) => {
    const out = spawnSync("python3", ["-B", join(skill, "scripts", "sai_tools.py"), join(TASK, "holdout", c.input), ...flags],
      { encoding: "utf8" });
    return checkResult(out.stdout, c.expected).score;
  });
  return { scores, mean: scores.reduce((a, b) => a + b, 0) / scores.length };
};
const overall = (skill, exec) => WEIGHTS.execution * exec + WEIGHTS.compliance * checkCompliance(skill).score
  + WEIGHTS.craft * checkCraft(skill).score;
const show = (label, skill, r) =>
  console.log(`# ${label} offline execution=${r.mean.toFixed(3)} overall=${overall(skill, r.mean).toFixed(3)} per-case=[${r.scores.map((s) => s.toFixed(2))}]`);
const NAIVE = [...solver.matchAll(/^    "([a-z_]+)": "/gm)].map((m) => m[1]);
const g = offline(good);
const b = offline(bad);
const n = offline(good, ["--naive", NAIVE.join(",")]);
show("good        ", good, g);
show("prior-edition", bad, b);
show("naive-reader", good, n);
expect("good fixture scores 1.0 on every holdout", g.scores.every((s) => s === 1));
expect("prior-edition fixture execution <= 0.65", b.mean <= 0.65);
expect("naive-reader execution <= 0.65", n.mean <= 0.65);

// Gold results are summaries; the reference solver must reproduce each one exactly.
const goldFits = readdirSync(join(TASK, "input")).filter((f) => /^applicant-\d+\.md$/.test(f)).every((f) => {
  const full = JSON.parse(spawnSync("python3", ["-B", join(good, "scripts", "sai_tools.py"), join(TASK, "input", f)],
    { encoding: "utf8" }).stdout);
  const gold = JSON.parse(readFileSync(join(TASK, "input", f.replace("applicant", "result").replace(".md", ".json")), "utf8"));
  return Object.keys(gold).every((k) => full[k] === gold[k]);
});
expect("gold results match the reference solver", goldFits);

if (failures.length) {
  console.log(`SELFTEST FAIL\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("SELFTEST PASS");
