#!/usr/bin/env node
// selftest-task2.mjs — offline proof that the task-2 probes discriminate. Must print SELFTEST PASS.
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  checkPurchaseOrder, checkCompliance, checkCraft, isExecutorFault, runWithRetry, aggregateExecution, INFRA_ERROR_RE,
} from "./score-task2.mjs";

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

const ok = { poWritten: true, timedOut: false, code: 0, raw: "{}" };
const fault = { poWritten: false, timedOut: false, code: 1, raw: "" };
const spun = { poWritten: false, timedOut: true, code: null, raw: "", transcript: "Reading catalog.csv…\nThinking…" };
const cutOff = { ...spun, transcript: "Reading catalog.csv…\nAPI Error: Connection closed" };
expect("non-zero exit without po.json is a fault", isExecutorFault(fault));
expect("timeout without po.json or an infra error is scored", !isExecutorFault(spun));
expect("timeout with an infra error is a fault", isExecutorFault(cutOff));
expect("timeout without po.json scores 0", checkPurchaseOrder(spun.raw, Object.values(key)[0].expected).score === 0);
expect("non-zero exit with po.json is scored", !isExecutorFault({ poWritten: true, timedOut: false, code: 1 }));
expect("clean exit without po.json is scored", !isExecutorFault({ poWritten: false, timedOut: false, code: 0 }));
const seq = (...outs) => { let i = 0; return async () => outs[i++]; };
const once = await runWithRetry(seq(fault, ok));
expect("one fault is retried and scored", once.attempts === 2 && !once.envFailure);
const twice = await runWithRetry(seq(fault, fault));
expect("two faults are an env failure", twice.attempts === 2 && twice.envFailure);
const clean = await runWithRetry(seq(ok, fault));
expect("a clean run is not retried", clean.attempts === 1 && !clean.envFailure);
const slow = await runWithRetry(seq(spun, spun));
expect("a timeout keeps its one retry, then scores 0", slow.attempts === 2 && !slow.envFailure && !slow.poWritten);
const recovered = await runWithRetry(seq(spun, ok));
expect("a timeout retried into a clean run is scored", recovered.attempts === 2 && recovered.poWritten);
const infra = await runWithRetry(seq(cutOff, cutOff));
expect("two infra timeouts are an env failure", infra.attempts === 2 && infra.envFailure);
const armPattern = readFileSync(join(HERE, "..", "arms", "run-arm.sh"), "utf8").match(/grep -qiE '([^']+)'/)?.[1];
expect("infra patterns match run-arm.sh", armPattern === INFRA_ERROR_RE.source && INFRA_ERROR_RE.flags === "i");
const one = { score: 1 };
const agg = aggregateExecution([
  { id: "h1", r: 0, envFailure: false, result: one }, { id: "h1", r: 1, envFailure: true, result: { score: 0 } },
  { id: "h2", r: 0, envFailure: true, result: { score: 0 } },
]);
expect("env-failed runs are excluded, never 0", agg.score === 1 && agg.cases.h1.runs.length === 1);
expect("env failures and excluded cases are reported", agg.env_failures.join() === "h1-r1,h2-r0" && agg.excluded_cases.join() === "h2");
expect("all runs env-failed gives no score", aggregateExecution([{ id: "h1", r: 0, envFailure: true, result: one }]).score === null);

if (failures.length) {
  console.log(`SELFTEST FAIL\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("SELFTEST PASS");
