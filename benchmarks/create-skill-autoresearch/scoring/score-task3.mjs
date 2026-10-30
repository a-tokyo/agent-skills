#!/usr/bin/env node
// score-task3.mjs — arm-independent scorer for a produced student-aid-index skill (task-3).
//
// Usage: node score-task3.mjs <path-to-skill-dir> [--executor-out <dir>] [--runs <n>]
//
// Scoring (weights fixed before any task-3 run):
//   execution  0.80  — a fixed AGENTIC executor (Haiku via `claude -p`, tools on) gets the produced
//                      skill installed as a user-level skill in a scratch HOME and a workspace holding
//                      one holdout applicant.md (never seen by any builder). It must write sai.json,
//                      which is checked field-by-field (exact integers, nulls included) against
//                      task-3/holdout/answer-key.json. No LLM judge anywhere.
//   compliance 0.15  — deterministic platform + brief constraints on the skill files
//   craft      0.05  — deterministic hybrid-architecture and description checks
//
// Execution carries more weight than in task-2 because task-3 exists to separate skills by what they
// get right on the holdouts; compliance and craft are near-saturated for every arm.
// The executor harness (isolation, retry, env-failure rule, process-group kill) is task-2's, imported.

import { readFileSync, existsSync, readdirSync, writeFileSync, mkdirSync, mkdtempSync, rmSync, cpSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import {
  parseFrontmatter, listFiles, spawnOwned, killOwned, runWithRetry, aggregateExecution,
} from "./score-task2.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const TASK = join(HERE, "..", "task-3");
export const EXECUTOR_MODEL = "claude-haiku-5-5";
export const WEIGHTS = { execution: 0.8, compliance: 0.15, craft: 0.05 };
// Every answer-key field is an exact integer, string or null: the methodology rounds every step to whole dollars.
export const FIELDS = ["dependency", "formula", "family_size", "max_pell_indicator", "min_pell_indicator",
  "income_protection_allowance", "payroll_tax_allowance", "available_income", "contribution_from_assets",
  "contribution", "sai"];

const same = (got, want) => (want === null ? got === null
  : typeof want === "number" ? got !== null && got !== "" && typeof got !== "boolean" && Number(got) === want
  : String(got ?? "").trim().toLowerCase() === String(want).toLowerCase());

// Pure: score one emitted sai.json against one expected result.
export function checkResult(raw, expected) {
  const checks = {};
  let q = null;
  try {
    q = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch {
    q = null;
  }
  checks.valid_json = !!q && typeof q === "object" && !Array.isArray(q);
  for (const f of FIELDS) checks[f] = checks.valid_json && same(q[f], expected[f]);
  const names = Object.keys(checks);
  const passed = names.filter((n) => checks[n]).length;
  return { checks, passed, total: names.length, score: passed / names.length };
}

// Gold-applicant literals a script must never embed: applicant ids and the gold SAI values.
function goldLiterals() {
  const lits = [];
  const dir = join(TASK, "input");
  for (const f of readdirSync(dir).filter((n) => /^applicant-\d+\.md$/.test(n))) {
    const id = readFileSync(join(dir, f), "utf8").match(/^Applicant: (\S+)/m)?.[1];
    if (id) lits.push(new RegExp(`["']${id}["']`));
  }
  for (const f of readdirSync(dir).filter((n) => /^result-\d+\.json$/.test(n))) {
    const sai = JSON.parse(readFileSync(join(dir, f), "utf8")).sai;
    if (Math.abs(sai) >= 100) lits.push(new RegExp(`(?<![0-9.,])${sai}(?![0-9])`));
  }
  return lits;
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
  checks.no_gold_answers = scripts.length > 0 && scripts.every((f) => {
    const src = readFileSync(f, "utf8");
    return !lits.some((l) => l.test(src));
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
  "Use the student-aid-index skill to compute the 2027-28 Student Aid Index for the FAFSA summary in applicant.md. " +
  "Write the result to sai.json in the current directory. Do not ask questions; work non-interactively.";

function runAgent(skillDir, skillName, caseInput, auth) {
  const root = mkdtempSync(join(tmpdir(), "csa-t3-exec-"));
  const home = join(root, "home");
  const work = join(root, "work");
  mkdirSync(join(home, ".claude", "skills"), { recursive: true });
  cpSync(skillDir, join(home, ".claude", "skills", skillName), { recursive: true });
  mkdirSync(work);
  cpSync(join(TASK, "holdout", caseInput), join(work, "applicant.md"));
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
      const outPath = join(work, "sai.json");
      // runWithRetry/isExecutorFault read `poWritten`: here it means sai.json was written.
      const poWritten = existsSync(outPath);
      const raw = poWritten ? readFileSync(outPath, "utf8") : "";
      if (spawnError) transcript += `\n[spawn error] ${spawnError.message}`;
      rmSync(root, { recursive: true, force: true });
      resolve({ raw, transcript, code, timedOut, poWritten });
    };
    child.on("error", (err) => finish(null, err));
    child.on("close", (code) => finish(code));
  });
}

export async function scoreSkill(skillDir, { apiKey, oauthToken, executorOut, runs = 1 } = {}) {
  const compliance = checkCompliance(skillDir);
  const craft = checkCraft(skillDir);
  const report = { skillDir, compliance, craft, execution: null, overall: null };
  let executionScore = 0;
  if (compliance.checks.skill_md_exists && (apiKey || oauthToken)) {
    const fm = parseFrontmatter(readFileSync(join(skillDir, "SKILL.md"), "utf8"));
    const skillName = /^[a-z0-9-]+$/.test(fm?.fm.name ?? "") ? fm.fm.name : "student-aid-index";
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
          writeFileSync(join(executorOut, "executor", `${stem}.sai.json`), out.raw);
        }
        return out;
      });
      return { id, r, envFailure: run.envFailure, result: checkResult(run.raw, c.expected) };
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
    console.error("usage: node score-task3.mjs <path-to-skill-dir> [--executor-out <dir>] [--runs <n>]");
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
