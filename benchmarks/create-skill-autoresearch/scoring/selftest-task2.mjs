#!/usr/bin/env node
// selftest-task2.mjs — offline proof that the task-2 probes discriminate. Must print SELFTEST PASS.
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { checkPurchaseOrder, checkCompliance, checkCraft } from "./score-task2.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const key = JSON.parse(readFileSync(join(HERE, "..", "task-2", "holdout", "answer-key.json"), "utf8"));
const failures = [];
const expect = (name, cond) => { if (!cond) failures.push(name); };

for (const [id, c] of Object.entries(key)) {
  expect(`${id} perfect = 1`, checkPurchaseOrder(JSON.stringify(c.expected), c.expected).score === 1);
  const wrongTier = { ...c.expected, approval: c.expected.approval === "auto" ? "manager" : "auto" };
  const r = checkPurchaseOrder(wrongTier, c.expected);
  expect(`${id} wrong tier loses exactly one check`, r.passed === r.total - 1);
  const nudged = structuredClone(c.expected);
  if (nudged.lines.length) nudged.lines[0].unit_price_eur += 0.05;
  expect(`${id} price off by 0.05 fails unit_prices`, !nudged.lines.length || !checkPurchaseOrder(nudged, c.expected).checks.unit_prices);
  if (c.expected.exceptions.length) {
    const wrongDetail = structuredClone(c.expected);
    wrongDetail.exceptions[0].detail = "something else";
    const d = checkPurchaseOrder(wrongDetail, c.expected);
    expect(`${id} wrong exception detail fails only exception_details`, !d.checks.exception_details && d.checks.exception_codes);
  }
  expect(`${id} garbage = 0`, checkPurchaseOrder("not json", c.expected).score === 0);
  expect(`${id} empty file = 0`, checkPurchaseOrder("", c.expected).score === 0);
}

const good = join(HERE, "fixtures", "t2-good-skill");
const bad = join(HERE, "fixtures", "t2-bad-skill");
expect("good compliance = 1", checkCompliance(good).score === 1);
expect("good craft = 1", checkCraft(good).score === 1);
expect("bad compliance <= 0.6", checkCompliance(bad).score <= 0.6);
expect("bad craft <= 0.5", checkCraft(bad).score <= 0.5);

if (failures.length) {
  console.log(`SELFTEST FAIL\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("SELFTEST PASS");
