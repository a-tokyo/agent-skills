#!/usr/bin/env node
// score-task2.mjs — arm-independent scorer for a produced purchase-order-builder skill (task-2).
//
// Usage: node score-task2.mjs <path-to-skill-dir> [--executor-out <dir>] [--runs <n>]
//
// Scoring (weights fixed before any benchmark run):
//   execution  0.70  — a fixed AGENTIC executor (Haiku via `claude -p`, tools on) gets the produced
//                      skill installed as a user-level skill in a scratch HOME and a workspace holding
//                      one holdout request.md + the HOLDOUT master data (never seen by any builder).
//                      It must write po.json, which is checked field-by-field against
//                      task-2/holdout/answer-key.json. No LLM judge anywhere.
//   compliance 0.20  — deterministic platform + brief constraints on the skill files
//   craft      0.10  — deterministic hybrid-architecture and description checks
//
// Execution needs ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN; compliance/craft run without them.
// An executor run that times out or exits non-zero without writing po.json is retried once. A second such
// run is an env failure — excluded and reported, never 0 — only on a positive infrastructure signature in
// its transcript: run-arm.sh's API/network patterns, an auth error, or claude failing to spawn. Without
// one, a timeout or a non-zero exit is the skill's result and scores 0.
// If no case has a scored run left, no overall_score is printed and the process exits 75.

import { readFileSync, existsSync, readdirSync, writeFileSync, mkdirSync, mkdtempSync, rmSync, cpSync, statSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { tmpdir } from "node:os";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TASK = join(HERE, "..", "task-2");
export const EXECUTOR_MODEL = "claude-haiku-5-5";
export const WEIGHTS = { execution: 0.7, compliance: 0.2, craft: 0.1 };
const MONEY_TOLERANCE = 0.011;

const near = (a, b) => typeof a === "number" && typeof b === "number" && Math.abs(a - b) <= MONEY_TOLERANCE;
const sameList = (a, b) => Array.isArray(a) && a.length === b.length && a.every((x, i) => x === b[i]);

// Pure: score one emitted purchase order against one expected purchase order.
export function checkPurchaseOrder(raw, expected) {
  const checks = {};
  let po = null;
  try {
    po = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch {
    po = null;
  }
  checks.valid_json = !!po && typeof po === "object";
  const lines = Array.isArray(po?.lines) ? po.lines : [];
  const want = expected.lines;
  checks.site_code = po?.site_code === expected.site_code;
  checks.cost_center = po?.cost_center === expected.cost_center;
  checks.skus = sameList(lines.map((l) => l?.sku), want.map((l) => l.sku));
  checks.vendors = sameList(lines.map((l) => l?.vendor_id), want.map((l) => l.vendor_id));
  checks.quantities = sameList(lines.map((l) => Number(l?.qty)), want.map((l) => l.qty));
  checks.unit_prices = lines.length === want.length && want.every((l, i) => near(Number(lines[i]?.unit_price_eur), l.unit_price_eur));
  checks.line_totals = lines.length === want.length && want.every((l, i) => near(Number(lines[i]?.line_total_eur), l.line_total_eur));
  checks.total = near(Number(po?.total_eur), expected.total_eur);
  checks.approval = po?.approval === expected.approval;
  checks.it_review = po?.it_review === expected.it_review;
  const codes = Array.isArray(po?.exceptions) ? po.exceptions.map((e) => e?.code) : null;
  checks.exception_codes = sameList(codes, expected.exceptions.map((e) => e.code));
  const details = Array.isArray(po?.exceptions) ? po.exceptions.map((e) => String(e?.detail ?? "").trim().toLowerCase()) : null;
  checks.exception_details = sameList(details, expected.exceptions.map((e) => e.detail.toLowerCase()));
  const names = Object.keys(checks);
  const passed = names.filter((n) => checks[n]).length;
  return { checks, passed, total: names.length, score: passed / names.length };
}

function parseFrontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!m) return null;
  const fm = {};
  let currentKey = null;
  for (const line of m[1].split("\n")) {
    const kv = line.match(/^([a-zA-Z-]+):[ \t]*(.*)$/);
    if (kv) {
      currentKey = kv[1];
      fm[currentKey] = kv[2].trim();
    } else if (currentKey && /^\s+\S/.test(line)) {
      fm[currentKey] = ((fm[currentKey] === ">-" || fm[currentKey] === ">" || fm[currentKey] === "|" ? "" : fm[currentKey]) + " " + line.trim()).trim();
    }
  }
  return { fm, body: text.slice(m[0].length) };
}

function listFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? listFiles(p) : [p];
  });
}

function goldLiterals() {
  const lits = new Set();
  const inputDir = join(TASK, "input");
  for (const f of readdirSync(inputDir).filter((f) => /^po-\d+\.json$/.test(f))) {
    const po = JSON.parse(readFileSync(join(inputDir, f), "utf8"));
    lits.add(po.site_code);
    lits.add(po.cost_center);
    for (const l of po.lines) lits.add(l.sku);
  }
  return [...lits];
}

// Pure given file contents: platform + brief compliance.
export function checkCompliance(skillDir) {
  const checks = {};
  const skillPath = join(skillDir, "SKILL.md");
  checks.skill_md_exists = existsSync(skillPath);
  if (!checks.skill_md_exists) return { checks, passed: 0, total: 10, score: 0 };
  const text = readFileSync(skillPath, "utf8");
  const parsed = parseFrontmatter(text);
  checks.frontmatter_parses = !!parsed && !!parsed.fm.name && !!parsed.fm.description;
  const name = parsed?.fm.name ?? "";
  checks.name_valid = name.length > 0 && name.length <= 64 && /^[a-z0-9-]+$/.test(name) && !/anthropic|claude/.test(name);
  const desc = parsed?.fm.description ?? "";
  checks.description_valid = desc.length > 0 && desc.length <= 1024;
  checks.model_invoked = (parsed?.fm["disable-model-invocation"] ?? "") !== "true";
  checks.body_under_300 = (parsed?.body ?? text).split("\n").length < 300;
  const scripts = listFiles(join(skillDir, "scripts")).filter((f) => /\.(py|sh|mjs|js)$/.test(f));
  checks.has_scripts = scripts.length > 0;
  checks.scripts_referenced = scripts.length > 0 && scripts.every((f) => text.includes(`scripts/${basename(f)}`));
  const refsDir = join(skillDir, "references");
  checks.refs_one_level = !existsSync(refsDir) || readdirSync(refsDir).every((f) => text.includes(`references/${f}`));
  const lits = goldLiterals();
  checks.no_hardcoded_master_data = scripts.length > 0 && scripts.every((f) => {
    const src = readFileSync(f, "utf8");
    return !lits.some((l) => src.includes(l));
  });
  const names = Object.keys(checks);
  const passed = names.filter((n) => checks[n]).length;
  return { checks, passed, total: names.length, score: passed / names.length };
}

// Pure given file contents: hybrid architecture + description craft.
export function checkCraft(skillDir) {
  const checks = {};
  const skillPath = join(skillDir, "SKILL.md");
  if (!existsSync(skillPath)) return { checks, passed: 0, total: 4, score: 0 };
  const text = readFileSync(skillPath, "utf8");
  const parsed = parseFrontmatter(text);
  const desc = parsed?.fm.description ?? "";
  checks.description_what_and_when = desc.length > 0 && /\bwhen\b/i.test(desc);
  checks.description_third_person = desc.length > 0 && !/^(i|you|use this skill)\b/i.test(desc.trim());
  const scripts = listFiles(join(skillDir, "scripts")).filter((f) => /\.(py|sh|mjs|js)$/.test(f));
  const defs = scripts.map((f) => readFileSync(f, "utf8"))
    .reduce((n, src) => n + (src.match(/^\s*(def |function |export function |[a-z_]+\(\) *\{)/gm) ?? []).length, 0);
  checks.granular_functions = defs >= 3;
  checks.concrete_example = /```/.test(parsed?.body ?? "");
  const names = Object.keys(checks);
  const passed = names.filter((n) => checks[n]).length;
  return { checks, passed, total: names.length, score: passed / names.length };
}

const EXECUTOR_PROMPT =
  "Use the purchase-order-builder skill to turn request.md into a purchase order, using the master data in master-data/. " +
  "Write the result to po.json in the current directory. Do not ask questions; work non-interactively.";

// The executor gets a process group of its own (POSIX) so a timeout, or the run's end, also stops what it
// started (a server, a detached helper) before the next run; on Windows `taskkill /T` ends its tree by PID.
export const spawnOwned = (cmd, args, opts) => spawn(cmd, args, { ...opts, detached: process.platform !== "win32" });

export function killOwned(child) {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    return;
  }
  try { process.kill(-child.pid, "SIGKILL"); } catch { /* the group has already exited */ }
}

function runAgent(skillDir, skillName, caseInput, auth) {
  const root = mkdtempSync(join(tmpdir(), "csa-t2-exec-"));
  const home = join(root, "home");
  const work = join(root, "work");
  mkdirSync(join(home, ".claude", "skills"), { recursive: true });
  cpSync(skillDir, join(home, ".claude", "skills", skillName), { recursive: true });
  mkdirSync(work);
  cpSync(join(TASK, "holdout", "master-data"), join(work, "master-data"), { recursive: true });
  cpSync(join(TASK, "holdout", caseInput), join(work, "request.md"));
  return new Promise((resolve) => {
    const env = { PATH: process.env.PATH, HOME: home, TERM: "dumb", LANG: "en_US.UTF-8", DISABLE_AUTOUPDATER: "1" };
    if (auth.apiKey) env.ANTHROPIC_API_KEY = auth.apiKey;
    else env.CLAUDE_CODE_OAUTH_TOKEN = auth.oauthToken;
    const child = spawnOwned("claude", ["-p", EXECUTOR_PROMPT, "--model", EXECUTOR_MODEL,
      "--dangerously-skip-permissions", "--max-turns", "30"], { cwd: work, env, stdio: ["ignore", "pipe", "pipe"] });
    let transcript = "";
    let timedOut = false;
    let settled = false;
    child.stdout.on("data", (d) => (transcript += d));
    child.stderr.on("data", (d) => (transcript += d));
    const timer = setTimeout(() => { timedOut = true; killOwned(child); }, 900000);
    const finish = (code, spawnError) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (!spawnError) killOwned(child);
      const poPath = join(work, "po.json");
      const poWritten = existsSync(poPath);
      const raw = poWritten ? readFileSync(poPath, "utf8") : "";
      if (spawnError) transcript += `\n[spawn error] ${spawnError.message}`;
      rmSync(root, { recursive: true, force: true });
      resolve({ raw, transcript, code, timedOut, poWritten });
    };
    child.on("error", (err) => finish(null, err));
    child.on("close", (code) => finish(code));
  });
}

// The infrastructure-error patterns arms/run-arm.sh greps build transcripts for; selftest-task2 keeps them equal.
export const INFRA_ERROR_RE =
  /ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|network error|socket hang up|503 Service|429 Too Many|fetch failed|API Error: Connection closed|overloaded_error/i;

// Pure: a run without po.json is worth one retry when it timed out or exited non-zero.
export const isRetryable = ({ poWritten, timedOut, code }) => !poWritten && (timedOut || code !== 0);

// Executor-only infrastructure signatures beyond run-arm.sh's: a rejected credential, or claude not starting.
export const EXECUTOR_INFRA_RE = /authentication_error|invalid api key|\[spawn error\]/i;

// Pure: a run without po.json that timed out or exited non-zero measured the environment, not the skill, only
// when its transcript carries an infrastructure signature; otherwise it is the skill's result and scores 0.
export const isExecutorFault = ({ poWritten, timedOut, code, transcript = "" }) =>
  !poWritten && (timedOut || code !== 0) && (INFRA_ERROR_RE.test(transcript) || EXECUTOR_INFRA_RE.test(transcript));

// Runs one executor job via `attempt(n)`, retrying a retryable run once; a second fault is an env failure.
export async function runWithRetry(attempt) {
  const first = await attempt(0);
  if (!isRetryable(first)) return { ...first, attempts: 1, envFailure: false };
  const second = await attempt(1);
  return { ...second, attempts: 2, envFailure: isExecutorFault(second) };
}

// Pure: per-case mean over scored runs. Env-failed runs are dropped, and a case left with none is excluded
// from the execution mean; both are listed. score is null when no case has a scored run.
export function aggregateExecution(results) {
  const cases = {};
  const envFailures = [];
  for (const { id, r, envFailure, result } of results) {
    if (envFailure) envFailures.push(`${id}-r${r}`);
    else (cases[id] ??= []).push(result);
  }
  const ids = [...new Set(results.map((x) => x.id))];
  const excludedCases = ids.filter((id) => !cases[id]);
  const perCase = Object.fromEntries(Object.entries(cases).map(([id, rs]) =>
    [id, { runs: rs, score: rs.reduce((s, x) => s + x.score, 0) / rs.length }]));
  const scored = Object.values(perCase);
  const score = scored.length ? scored.reduce((s, c) => s + c.score, 0) / scored.length : null;
  return { cases: perCase, score, env_failures: envFailures, excluded_cases: excludedCases };
}

export async function scoreSkill(skillDir, { apiKey, oauthToken, executorOut, runs = 1 } = {}) {
  const compliance = checkCompliance(skillDir);
  const craft = checkCraft(skillDir);
  const report = { skillDir, compliance, craft, execution: null, overall: null };
  let executionScore = 0;
  if (compliance.checks.skill_md_exists && (apiKey || oauthToken)) {
    const fm = parseFrontmatter(readFileSync(join(skillDir, "SKILL.md"), "utf8"));
    const skillName = /^[a-z0-9-]+$/.test(fm?.fm.name ?? "") ? fm.fm.name : "purchase-order-builder";
    const key = JSON.parse(readFileSync(join(TASK, "holdout", "answer-key.json"), "utf8"));
    const jobs = [];
    for (const [id, c] of Object.entries(key)) {
      for (let r = 0; r < runs; r++) jobs.push({ id, r, c });
    }
    const results = await Promise.all(jobs.map(async ({ id, r, c }) => {
      const run = await runWithRetry(async (n) => {
        const out = await runAgent(skillDir, skillName, c.input, { apiKey, oauthToken });
        if (executorOut) {
          const stem = `${id}-r${r}${n ? "-retry" : ""}`;
          mkdirSync(join(executorOut, "executor"), { recursive: true });
          writeFileSync(join(executorOut, "executor", `${stem}.txt`), out.transcript);
          writeFileSync(join(executorOut, "executor", `${stem}.po.json`), out.raw);
        }
        return out;
      });
      return { id, r, envFailure: run.envFailure, result: checkPurchaseOrder(run.raw, c.expected) };
    }));
    const execution = aggregateExecution(results);
    executionScore = execution.score;
    report.execution = { ...execution, executor: apiKey ? "claude-cli+api-key" : "claude-cli+oauth" };
  } else if (!apiKey && !oauthToken) {
    console.error("WARN: no ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN — execution scored 0; compliance/craft only");
  }
  report.overall = executionScore === null ? null
    : WEIGHTS.execution * executionScore + WEIGHTS.compliance * compliance.score + WEIGHTS.craft * craft.score;
  if (executorOut) {
    mkdirSync(executorOut, { recursive: true });
    writeFileSync(join(executorOut, "score-report.json"), JSON.stringify(report, null, 2) + "\n");
  }
  return report;
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  const skillDir = process.argv[2];
  if (!skillDir) {
    console.error("usage: node score-task2.mjs <path-to-skill-dir> [--executor-out <dir>] [--runs <n>]");
    process.exit(2);
  }
  const arg = (flag) => (process.argv.indexOf(flag) > -1 ? process.argv[process.argv.indexOf(flag) + 1] : null);
  let oauthToken = process.env.CLAUDE_CODE_OAUTH_TOKEN;
  const tokenFile = join(HERE, "..", ".auth-token");
  if (!oauthToken && existsSync(tokenFile)) oauthToken = readFileSync(tokenFile, "utf8").trim();
  const report = await scoreSkill(skillDir, {
    apiKey: process.env.ANTHROPIC_API_KEY, oauthToken, executorOut: arg("--executor-out"), runs: Number(arg("--runs") ?? 1),
  });
  console.log(`METRIC compliance=${report.compliance.score.toFixed(3)}`);
  console.log(`METRIC craft=${report.craft.score.toFixed(3)}`);
  for (const [id, c] of Object.entries(report.execution?.cases ?? {})) {
    const failed = [...new Set(c.runs.flatMap((r) => Object.entries(r.checks).filter(([, v]) => !v).map(([k]) => k)))];
    console.log(`# ${id}: ${c.score.toFixed(3)}${failed.length ? ` failed=[${failed.join(",")}]` : ""}`);
  }
  if (report.execution?.env_failures.length) {
    console.log(`# env_failures=[${report.execution.env_failures.join(",")}] excluded_cases=[${report.execution.excluded_cases.join(",")}]`);
  }
  if (report.overall === null) {
    console.log("ENV_FAILURE: every executor run faulted twice; not scored");
    process.exit(75);
  }
  console.log(`METRIC execution=${(report.execution?.score ?? 0).toFixed(3)}`);
  console.log(`METRIC overall_score=${report.overall.toFixed(3)}`);
}
