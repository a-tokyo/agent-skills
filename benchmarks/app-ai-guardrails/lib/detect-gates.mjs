// lib/detect-gates.mjs — shared stack + gate-command detection.
//
// Single source of truth for "how do I find the 7 gate commands in this repo", used by both
// evaluate.sh (via the CLI below) and check-guardrails.mjs (via direct import) so the two never
// drift on parsing rules. Detection points per DESIGN.md §8.1: package.json `scripts` keys /
// `[tool.poe.tasks]` / justfile recipes / `.cargo/config.toml` `[alias]` keys /
// `build.gradle`(`.kts`) `tasks.register(...)` names (springboot; `test`/`build` are Gradle's own
// always-present native lifecycle tasks, so detection never needs to run java/gradle).
//
// CLI usage: node lib/detect-gates.mjs <repo-dir> [--stack <name>]
//   -> prints {"stack":"next","runner_prefix":"npm run","gates":{"lint":"npm run lint", ...}}
//   missing gates are `null`.

import fs from 'node:fs';
import path from 'node:path';

export const GATE_NAMES = ['lint', 'typecheck', 'test', 'coverage', 'build', 'e2e', 'audit'];

function readFileSafe(p) {
  try { return fs.readFileSync(p, 'utf8'); } catch { return null; }
}

function readJsonSafe(p) {
  const txt = readFileSafe(p);
  if (txt == null) return null;
  try { return JSON.parse(txt); } catch { return null; }
}

export function detectStack(repoDir) {
  const pkg = readJsonSafe(path.join(repoDir, 'package.json'));
  if (pkg) {
    const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
    if (deps.next || fs.existsSync(path.join(repoDir, 'next.config.ts')) || fs.existsSync(path.join(repoDir, 'next.config.js')) || fs.existsSync(path.join(repoDir, 'next.config.mjs'))) return 'next';
    if (deps['@nestjs/core'] || fs.existsSync(path.join(repoDir, 'nest-cli.json'))) return 'nest';
    return 'next'; // package.json present but neither signal found -> default JS/TS path is next
  }
  if (fs.existsSync(path.join(repoDir, 'pyproject.toml')) || fs.existsSync(path.join(repoDir, 'manage.py'))) return 'django';
  if (fs.existsSync(path.join(repoDir, 'go.mod'))) return 'go';
  if (fs.existsSync(path.join(repoDir, 'Cargo.toml'))) return 'rust';
  const gradleFile = path.join(repoDir, 'build.gradle');
  const gradleKtsFile = path.join(repoDir, 'build.gradle.kts');
  if (fs.existsSync(gradleFile) || fs.existsSync(gradleKtsFile)) return 'springboot';
  return null;
}

export function runnerPrefix(stack) {
  return { next: 'npm run', nest: 'npm run', django: 'uv run poe', go: 'just', rust: 'cargo', springboot: './gradlew' }[stack] || null;
}

// ---- minimal parsers (hand-rolled; only need to survive our own generated shapes + common flat forms) ----

// Parses `[tool.poe.tasks]` flat entries: `name = "cmd"` or `name = { cmd = "..." }` (first quoted string wins).
// Also tolerates the sub-table form `[tool.poe.tasks.name]\ncmd = "..."`.
function parsePoeTasks(toml) {
  const out = {};
  if (!toml) return out;
  const lines = toml.split(/\r?\n/);
  let inFlatSection = false;
  let curSubTask = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const sectionMatch = line.match(/^\[(.+)\]$/);
    if (sectionMatch) {
      const name = sectionMatch[1];
      if (name === 'tool.poe.tasks') { inFlatSection = true; curSubTask = null; continue; }
      const subMatch = name.match(/^tool\.poe\.tasks\.([A-Za-z0-9_:-]+)$/);
      if (subMatch) { inFlatSection = false; curSubTask = subMatch[1]; continue; }
      inFlatSection = false; curSubTask = null;
      continue;
    }
    if (inFlatSection) {
      const kv = line.match(/^([A-Za-z0-9_:-]+)\s*=\s*(.+)$/);
      if (!kv) continue;
      const key = kv[1];
      const strMatch = kv[2].match(/"((?:[^"\\]|\\.)*)"/);
      if (strMatch) out[key] = strMatch[1];
      else out[key] = kv[2]; // table form on one line; store raw, presence is what matters
    } else if (curSubTask) {
      const kv = line.match(/^(cmd|shell|sequence)\s*=\s*"((?:[^"\\]|\\.)*)"/);
      if (kv && !out[curSubTask]) out[curSubTask] = kv[2];
      else if (!out[curSubTask]) out[curSubTask] = true; // sub-table present but cmd form unrecognized; presence only
    }
  }
  return out;
}

// Parses `.cargo/config.toml` `[alias]` section: `name = "expansion"`.
function parseCargoAlias(toml) {
  const out = {};
  if (!toml) return out;
  let inAlias = false;
  for (const raw of toml.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const sectionMatch = line.match(/^\[(.+)\]$/);
    if (sectionMatch) { inAlias = sectionMatch[1] === 'alias'; continue; }
    if (!inAlias) continue;
    const kv = line.match(/^([A-Za-z0-9_:-]+)\s*=\s*"((?:[^"\\]|\\.)*)"/);
    if (kv) out[kv[1]] = kv[2];
  }
  return out;
}

// Parses justfile recipe names: a non-indented, non-comment line starting an identifier followed by
// optional params then `:`. Recipe *bodies* are irrelevant to detection — only names.
function parseJustfileRecipes(text) {
  const out = new Set();
  if (!text) return out;
  for (const raw of text.split(/\r?\n/)) {
    if (/^\s/.test(raw)) continue; // indented = recipe body
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith('@') || line.startsWith('set ') || line.startsWith('import') || line.startsWith('[')) continue;
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_-]*)\b[^:]*:(?!=)/);
    if (m) out.add(m[1]);
  }
  return out;
}

// Parses `build.gradle`/`build.gradle.kts` for `tasks.register('name', ...)` / `tasks.register("name", ...)`
// custom task declarations. Only NAMES matter (task bodies are irrelevant to detection). This finds
// the springboot adapter's custom-registered gates (lint/typecheck/coverage/e2e/audit); Gradle's own
// native `test` and `build` lifecycle tasks are never explicitly registered so they are NOT found by
// this parser -- they are treated as always-present by the caller once a Gradle project is confirmed
// (springboot.md keeps `build` as Gradle's own native lifecycle task, deliberately unmodified).
function parseGradleTasks(text) {
  const out = new Set();
  if (!text) return out;
  const re = /tasks\.register\(\s*['"]([A-Za-z0-9_-]+)['"]/g;
  let m;
  while ((m = re.exec(text))) out.add(m[1]);
  return out;
}

export function detectGates(repoDir, stack) {
  const gates = {};
  const prefix = runnerPrefix(stack);
  if (stack === 'next' || stack === 'nest') {
    const pkg = readJsonSafe(path.join(repoDir, 'package.json')) || {};
    const scripts = pkg.scripts || {};
    for (const g of GATE_NAMES) gates[g] = scripts[g] ? `${prefix} ${g}` : null;
  } else if (stack === 'django') {
    const toml = readFileSafe(path.join(repoDir, 'pyproject.toml'));
    const tasks = parsePoeTasks(toml);
    for (const g of GATE_NAMES) gates[g] = tasks[g] ? `${prefix} ${g}` : null;
  } else if (stack === 'go') {
    const jf = readFileSafe(path.join(repoDir, 'justfile')) || readFileSafe(path.join(repoDir, 'Justfile'));
    const recipes = parseJustfileRecipes(jf);
    for (const g of GATE_NAMES) gates[g] = recipes.has(g) ? `${prefix} ${g}` : null;
  } else if (stack === 'rust') {
    const toml = readFileSafe(path.join(repoDir, '.cargo', 'config.toml'));
    const aliases = parseCargoAlias(toml);
    // `test` and `build` are cargo BUILT-INS — cargo refuses aliases that shadow built-in commands,
    // so canon-correct repos have no [alias] entry for them yet the gates exist (same shape as
    // Gradle's native lifecycle tasks below; found 2026-07-06 on rust run 1, where detection said
    // "missing" while `cargo test`/`cargo build` ran green by hand).
    const rustGates = new Set(Object.keys(aliases));
    rustGates.add('test');
    rustGates.add('build');
    for (const g of GATE_NAMES) gates[g] = rustGates.has(g) ? `${prefix} ${g}` : null;
  } else if (stack === 'springboot') {
    const gradle = readFileSafe(path.join(repoDir, 'build.gradle')) || readFileSafe(path.join(repoDir, 'build.gradle.kts'));
    const tasks = parseGradleTasks(gradle);
    // `test` and `build` are Gradle's own native lifecycle tasks (from the `java`/base plugins) --
    // never explicitly `tasks.register`-ed, so they are always present once build.gradle exists at
    // all (this is what lets detection work WITHOUT running java: no execution, pure text parse).
    tasks.add('test');
    tasks.add('build');
    for (const g of GATE_NAMES) gates[g] = tasks.has(g) ? `${prefix} ${g}` : null;
  } else {
    for (const g of GATE_NAMES) gates[g] = null;
  }
  return gates;
}

// ---- CLI ----
if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const repoDir = args[0];
  if (!repoDir) { console.error('usage: detect-gates.mjs <repo-dir> [--stack <name>]'); process.exit(2); }
  let stack = null;
  const stackIdx = args.indexOf('--stack');
  if (stackIdx !== -1) stack = args[stackIdx + 1];
  if (!stack) stack = detectStack(repoDir);
  if (!stack) { console.error(`FATAL: could not detect stack for ${repoDir}`); process.exit(3); }
  const gates = detectGates(repoDir, stack);
  console.log(JSON.stringify({ stack, runner_prefix: runnerPrefix(stack), gates }));
}
