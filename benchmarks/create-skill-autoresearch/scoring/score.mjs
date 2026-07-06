#!/usr/bin/env node
// score.mjs — arm-independent scorer for a produced conventional-commits skill.
//
// Usage: node score.mjs <path-to-skill-dir> [--executor-out <dir>]
//   <path-to-skill-dir> must contain SKILL.md (the produced skill).
//
// Scoring (weights fixed before any benchmark run):
//   execution  0.60  — a fixed executor model (Haiku, temperature 0) receives the produced
//                      SKILL.md as its system prompt and each holdout diff summary as the user
//                      message; the emitted commit message is parsed field-by-field against
//                      task/holdout/answer-key.json. No LLM judge anywhere.
//   compliance 0.25  — deterministic platform + brief constraints on the skill files
//   craft      0.15  — deterministic user-invoked description craft + concrete-example checks
//
// Emits METRIC lines to stdout and a JSON report to stderr-adjacent file if --executor-out given.
// Requires ANTHROPIC_API_KEY for the execution dimension (compliance/craft run without it).

import { readFileSync, existsSync, readdirSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TASK = join(HERE, "..", "task");
export const EXECUTOR_MODEL = "claude-haiku-4-5-20251001";
export const WEIGHTS = { execution: 0.6, compliance: 0.25, craft: 0.15 };

const TYPES = ["feat", "fix", "build", "chore", "ci", "docs", "style", "refactor", "perf", "test"];
const FIRST_LINE_RE = new RegExp(`^(${TYPES.join("|")})(\\(([a-z0-9-]+)\\))?(!)?: (.+)$`);
const REVERT_RE = new RegExp(`^revert: (${TYPES.join("|")})(\\(([a-z0-9-]+)\\))?(!)?: (.+)$`);
const NON_IMPERATIVE = new Set([
  "added", "adds", "fixed", "fixes", "updated", "updates", "removed", "removes",
  "changed", "changes", "bumped", "bumps", "reverted", "reverts", "refactored", "refactors",
]);

export function parseFirstLine(line) {
  const revert = line.match(REVERT_RE);
  if (revert) {
    return { revert: true, innerType: revert[1], innerScope: revert[3] ?? null, bang: !!revert[4], subject: revert[5] };
  }
  const m = line.match(FIRST_LINE_RE);
  if (!m) return null;
  return { revert: false, type: m[1], scope: m[3] ?? null, bang: !!m[4], subject: m[5] };
}

// Pure: score one emitted commit message against one answer-key case.
export function checkCommitMessage(raw, key) {
  const msg = raw.trim().replace(/^```[a-z]*\n?/, "").replace(/\n?```$/, "").trim();
  const lines = msg.split("\n");
  const first = lines[0];
  const body = lines.slice(1).join("\n");
  const checks = {};
  const parsed = parseFirstLine(first);
  const wantRevert = !!key.revert;

  checks.format_valid = parsed !== null;
  if (wantRevert) {
    checks.type_match = !!parsed?.revert && key.revert.inner_types.includes(parsed.innerType);
    checks.scope_match = parsed?.innerScope === key.revert.inner_scope;
    checks.revert_body = new RegExp(`This reverts commit ${key.revert.sha}`).test(body);
    checks.revert_keywords = key.revert.inner_keywords.every((k) => first.toLowerCase().includes(k.toLowerCase()));
  } else {
    checks.type_match = !parsed?.revert && key.expected_types.includes(parsed?.type);
    if (key.expected_scope !== null) checks.scope_match = parsed?.scope === key.expected_scope;
    checks.breaking_bang = !!parsed?.bang === key.breaking;
    checks.breaking_footer = /^BREAKING CHANGE:/m.test(body) === key.breaking;
  }
  const subject = parsed ? (parsed.revert ? first : parsed.subject) : first;
  checks.subject_style =
    subject === subject.replace(/^[A-Z]/, (c) => c.toLowerCase()) &&
    !subject.endsWith(".") &&
    first.length <= 72 + (parsed?.revert ? 8 : 0) &&
    !NON_IMPERATIVE.has((parsed?.revert ? parsed.subject ?? subject : subject).split(/\s+/)[0]?.toLowerCase());
  checks.bullets = /^- /m.test(body) === key.expect_bullets;
  if (key.subject_keywords.length > 0) {
    checks.subject_keywords = key.subject_keywords.every((k) => first.toLowerCase().includes(k.toLowerCase()));
  }

  const names = Object.keys(checks);
  const passed = names.filter((n) => checks[n]).length;
  return { checks, passed, total: names.length, score: passed / names.length, message: msg };
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
      fm[currentKey] = ((fm[currentKey] === ">-" || fm[currentKey] === ">" ? "" : fm[currentKey]) + " " + line.trim()).trim();
      fm[`${currentKey}__multiline`] = true;
    }
  }
  return { fm, body: text.slice(m[0].length) };
}

// Pure given file contents: platform + brief compliance.
export function checkCompliance(skillDir) {
  const checks = {};
  const skillPath = join(skillDir, "SKILL.md");
  checks.skill_md_exists = existsSync(skillPath);
  if (!checks.skill_md_exists) {
    return { checks, passed: 0, total: 8, score: 0 };
  }
  const text = readFileSync(skillPath, "utf8");
  const parsed = parseFrontmatter(text);
  checks.frontmatter_parses = !!parsed && !!parsed.fm.name && !!parsed.fm.description;
  const name = parsed?.fm.name ?? "";
  checks.name_valid = name.length > 0 && name.length <= 64 && /^[a-z0-9-]+$/.test(name) && !/anthropic|claude/.test(name);
  const desc = parsed?.fm.description ?? "";
  checks.description_valid = desc.length > 0 && desc.length <= 1024;
  checks.user_invoked = (parsed?.fm["disable-model-invocation"] ?? "") === "true";
  checks.body_under_150 = (parsed?.body ?? text).split("\n").length < 150;
  checks.forward_slash_paths = !/\\\w+\\/.test(text);
  const refsDir = join(skillDir, "references");
  checks.refs_one_level = !existsSync(refsDir) || readdirSync(refsDir).every((f) => text.includes(`references/${f}`));

  const names = Object.keys(checks);
  const passed = names.filter((n) => checks[n]).length;
  return { checks, passed, total: names.length, score: passed / names.length };
}

// Pure given file contents: user-invoked description craft + concrete examples.
export function checkCraft(skillDir) {
  const checks = {};
  const skillPath = join(skillDir, "SKILL.md");
  if (!existsSync(skillPath)) return { checks, passed: 0, total: 3, score: 0 };
  const text = readFileSync(skillPath, "utf8");
  const parsed = parseFrontmatter(text);
  const desc = parsed?.fm.description ?? "";
  // user-invoked description = one-line human summary, trigger lists stripped
  checks.description_one_line = desc.length > 0 && desc.length <= 200 && !parsed?.fm.description__multiline;
  checks.no_trigger_scaffolding = !/use when|use for|use this skill|triggers on|when the user mentions/i.test(desc);
  const body = parsed?.body ?? "";
  checks.concrete_example = /```/.test(body) || body.split("\n").some((l) => parseFirstLine(l.trim()) !== null);

  const names = Object.keys(checks);
  const passed = names.filter((n) => checks[n]).length;
  return { checks, passed, total: names.length, score: passed / names.length };
}

const EXECUTOR_PROMPT = (input) =>
  `${input}\n\nUsing the conventional-commits skill above, reply with ONLY the commit message text — no code fences, no commentary.`;

// CLI executor: same contract as the API path (SKILL.md as system prompt), run through
// `claude -p --system-prompt` under a scratch HOME so the maintainer's skills/config never leak in.
// Used when only a CLAUDE_CODE_OAUTH_TOKEN (subscription auth) is available. Temperature is not
// pinnable through the CLI; the answer-key checks are coarse enough that this does not flip results.
function runExecutorCli(skillText, input, oauthToken) {
  const home = mkdtempSync(join(tmpdir(), "csa-executor-"));
  try {
    return execFileSync(
      "claude",
      ["-p", EXECUTOR_PROMPT(input), "--model", EXECUTOR_MODEL, "--system-prompt", skillText, "--max-turns", "1"],
      // allowlisted env only — the caller's unrelated secrets must never reach the executor session
      {
        env: { PATH: process.env.PATH, HOME: home, TERM: "dumb", CLAUDE_CODE_OAUTH_TOKEN: oauthToken },
        encoding: "utf8", timeout: 240000, stdio: ["ignore", "pipe", "pipe"],
      },
    );
  } catch (err) {
    // The CLI sometimes produces the full answer, then hangs on cleanup until the timeout.
    // The answer is complete (single-turn text) — salvage it rather than failing the case.
    if (err.stdout && err.stdout.trim().length > 0) return err.stdout;
    throw err;
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

async function runExecutor(skillText, input, apiKey) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "x-api-key": apiKey, "anthropic-version": "2023-06-01", "content-type": "application/json" },
    body: JSON.stringify({
      model: EXECUTOR_MODEL,
      max_tokens: 512,
      temperature: 0,
      system: skillText,
      messages: [{ role: "user", content: EXECUTOR_PROMPT(input) }],
    }),
  });
  if (!res.ok) throw new Error(`executor API ${res.status}: ${await res.text()}`);
  const data = await res.json();
  return data.content.map((b) => b.text ?? "").join("");
}

export async function scoreSkill(skillDir, { apiKey, oauthToken, executorOut } = {}) {
  const compliance = checkCompliance(skillDir);
  const craft = checkCraft(skillDir);
  const report = { skillDir, compliance, craft, execution: null, overall: null };

  let executionScore = 0;
  if (compliance.checks.skill_md_exists && (apiKey || oauthToken)) {
    const skillText = readFileSync(join(skillDir, "SKILL.md"), "utf8");
    const key = JSON.parse(readFileSync(join(TASK, "holdout", "answer-key.json"), "utf8"));
    const cases = {};
    for (const [id, caseKey] of Object.entries(key)) {
      const input = readFileSync(join(TASK, "holdout", caseKey.input), "utf8");
      const raw = apiKey ? await runExecutor(skillText, input, apiKey) : runExecutorCli(skillText, input, oauthToken);
      cases[id] = checkCommitMessage(raw, caseKey);
    }
    executionScore = Object.values(cases).reduce((s, c) => s + c.score, 0) / Object.keys(cases).length;
    report.execution = { cases, score: executionScore, executor: apiKey ? "api" : "claude-cli" };
  } else if (!apiKey && !oauthToken) {
    console.error("WARN: no ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN — execution dimension scored 0; compliance/craft only");
  }

  report.overall =
    WEIGHTS.execution * executionScore + WEIGHTS.compliance * compliance.score + WEIGHTS.craft * craft.score;

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
    console.error("usage: node score.mjs <path-to-skill-dir> [--executor-out <dir>]");
    process.exit(2);
  }
  const outIdx = process.argv.indexOf("--executor-out");
  const executorOut = outIdx > -1 ? process.argv[outIdx + 1] : null;
  let oauthToken = process.env.CLAUDE_CODE_OAUTH_TOKEN;
  const tokenFile = join(HERE, "..", ".auth-token");
  if (!oauthToken && existsSync(tokenFile)) oauthToken = readFileSync(tokenFile, "utf8").trim();
  const report = await scoreSkill(skillDir, { apiKey: process.env.ANTHROPIC_API_KEY, oauthToken, executorOut });
  console.log(`METRIC compliance=${report.compliance.score.toFixed(3)}`);
  console.log(`METRIC craft=${report.craft.score.toFixed(3)}`);
  console.log(`METRIC execution=${(report.execution?.score ?? 0).toFixed(3)}`);
  console.log(`METRIC overall_score=${report.overall.toFixed(3)}`);
  if (report.execution) {
    for (const [id, c] of Object.entries(report.execution.cases)) {
      const failed = Object.entries(c.checks).filter(([, v]) => !v).map(([k]) => k);
      console.log(`# ${id}: ${c.passed}/${c.total}${failed.length ? ` failed=[${failed.join(",")}]` : ""}`);
    }
  }
}
