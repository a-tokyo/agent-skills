#!/usr/bin/env node
// check-guardrails.mjs — deterministic, no-LLM rubric scorer for the app-ai-guardrails benchmark.
//
// Implements the rubric from research/00-synthesis.md §c (weights recorded as CRAFT-DECISIONS D11).
// Never calls an LLM. Every probe is a file read, a config parse, or a subprocess exit code. Exits 3
// on internal error (bad input, crash) so a bug never silently reports guardrail_score=0 as if it were
// a real (low) score — 0 is a legitimate score for a bare arm; 3 means "the scorer itself broke".
//
// Usage:
//   node check-guardrails.mjs <repo-dir> --stack <next|nest|django|go|rust|springboot> --gates <json>
//
// <json> (produced by evaluate.sh, see its header) shape:
//   { "<gate>": { "cmd": "npm run lint"|null, "exit": 0, "mode": "executed"|"fallback"|"missing" }, ... }
// for all 7 GATE_NAMES. "fallback" = e2e/audit config-present check substituted for real execution
// (no --e2e flag / no network); "missing" = no gate entry found in the repo at all.
//
// Output: one `METRIC <name>=<value>` line per probe/category/total, to stdout. Human-readable detail
// (which files were checked, teeth-mutation outcomes) goes to stderr and is never parsed by callers.

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { GATE_NAMES, detectStack } from './lib/detect-gates.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

function fail(msg) {
  console.error(`FATAL: ${msg}`);
  process.exit(3);
}

function parseArgs(argv) {
  const out = { repoDir: null, stack: null, gates: null };
  const rest = [...argv];
  out.repoDir = rest.shift();
  while (rest.length) {
    const a = rest.shift();
    if (a === '--stack') out.stack = rest.shift();
    else if (a === '--gates') out.gates = rest.shift();
    else fail(`unknown arg: ${a}`);
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
if (!args.repoDir) fail('usage: check-guardrails.mjs <repo-dir> --stack <s> --gates <json>');
const repoDir = path.resolve(args.repoDir);
if (!fs.existsSync(repoDir)) fail(`repo-dir does not exist: ${repoDir}`);
const stack = args.stack || detectStack(repoDir);
if (!stack) fail(`could not detect stack for ${repoDir} and none given via --stack`);
if (!args.gates) fail('missing --gates <json>');
let gatesInput;
try { gatesInput = JSON.parse(args.gates); } catch (e) { fail(`--gates is not valid JSON: ${e.message}`); }
for (const g of GATE_NAMES) {
  if (!gatesInput[g]) gatesInput[g] = { cmd: null, exit: null, mode: 'missing' };
}

// ---------- generic helpers ----------
function readFileSafe(p) { try { return fs.readFileSync(p, 'utf8'); } catch { return null; } }
function readJsonSafe(p) { const t = readFileSafe(p); if (t == null) return null; try { return JSON.parse(t); } catch { return null; } }
function exists(...segs) { return fs.existsSync(path.join(repoDir, ...segs)); }
function grepAny(text, patterns) { if (text == null) return false; return patterns.some((p) => (p instanceof RegExp ? p.test(text) : text.includes(p))); }

function run(cmd, cwd, timeoutMs = 180000) {
  const res = spawnSync(cmd, { cwd, shell: true, stdio: ['ignore', 'pipe', 'pipe'], timeout: timeoutMs, encoding: 'utf8' });
  return { status: res.status, signal: res.signal, stdout: res.stdout || '', stderr: res.stderr || '', timedOut: res.error && res.error.code === 'ETIMEDOUT' };
}

function gitLsFiles(dir) {
  const res = spawnSync('git', ['ls-files'], { cwd: dir, encoding: 'utf8' });
  if (res.status !== 0) return [];
  return res.stdout.split('\n').filter(Boolean);
}

// ---------- scoring bookkeeping ----------
const lines = [];
let raw = 0;
function metric(name, value) { lines.push(`METRIC ${name}=${value}`); }
function probe(catKey, name, points, passed, note) {
  const earned = passed ? points : 0;
  raw += earned;
  metric(`${catKey}__${name}`, earned);
  if (note) console.error(`  [${catKey}/${name}] ${passed ? 'PASS' : 'FAIL'} (${earned}/${points}) — ${note}`);
  else console.error(`  [${catKey}/${name}] ${passed ? 'PASS' : 'FAIL'} (${earned}/${points})`);
  return earned;
}

console.error(`# check-guardrails.mjs — stack=${stack} repo=${repoDir}`);

// ============================================================================
// Cat 1 — Executable gates run green (40)
// ============================================================================
const CAT1_POINTS = { typecheck: 6, lint: 6, test: 6, coverage: 8, build: 5, e2e: 5, audit: 4 };
let cat1 = 0;
for (const g of GATE_NAMES) {
  const info = gatesInput[g];
  const pass = info.mode !== 'missing' && info.exit === 0;
  cat1 += probe('gate', `${g}_green`, CAT1_POINTS[g], pass, `mode=${info.mode} exit=${info.exit} cmd=${info.cmd}`);
}
void cat1; // subtotal recomputed from probe lines at the end (single source of truth)

const allGatesPass = GATE_NAMES.every((g) => gatesInput[g].mode !== 'missing' && gatesInput[g].exit === 0);
metric('all_gates_pass_raw', allGatesPass ? 1 : 0);

// ============================================================================
// Cat 2 — Teeth / mutation probes (15). Mutate a temp COPY, run the gate, expect nonzero, discard copy.
// ============================================================================
// Per-extension mutation snippets. Appended to the END of an existing, already-exercised source file so
// coverage tools that only instrument imported/covered files still see the new (uncovered) code.
// IMPORTANT — mutation identifiers must NOT start with `_`: the canon eslint config ignores `^_`
// vars (unused-imports varsIgnorePattern) and rustc suppresses unused warnings on `_`-prefixed
// items, so an underscore-named probe tests nothing (false teeth failure on canon-compliant repos;
// found 2026-07-03 on with-skill runs, all of which "failed" teeth__lint for exactly this reason).
const MUTATIONS = {
  '.ts': {
    lint: '\nconst teethUnusedProbeXyz = 1;\n',
    typecheck: '\nconst teethTypeErrorProbe: number = "not-a-number";\n',
  },
  '.tsx': null, // filled below (identical to .ts)
  '.py': {
    lint: '\nimport os as teeth_unused_os_probe\n',
    typecheck: '\ndef teeth_type_error_probe(x: int) -> int:\n    return "not-an-int"\n',
  },
  '.go': {
    lint: '\nvar teethUnusedProbe = 1\n',
    typecheck: '\nvar teethTypeErrorProbe int = "not-a-number"\n',
  },
  '.rs': {
    lint: '\nfn teeth_unused_probe() {\n    let teeth_unused_local = 1;\n}\n',
    typecheck: '\nconst TEETH_TYPE_ERROR_PROBE: i32 = "not-a-number";\n',
  },
  '.java': {
    // Appended AFTER the target file's final `}` -- Java allows more than one top-level
    // (package-private) class per .java file, so a second standalone class here is valid syntax
    // without needing to splice into the existing (public) class body.
    lint: '\nclass TeethUnusedProbe {\n    private int teethUnusedField = 1;\n}\n',
    typecheck: '\nclass TeethTypeErrorProbe {\n    int x = "not-a-number";\n}\n',
  },
};
MUTATIONS['.tsx'] = MUTATIONS['.ts'];

const EXT_BY_STACK = { next: ['.ts', '.tsx'], nest: ['.ts'], django: ['.py'], go: ['.go'], rust: ['.rs'], springboot: ['.java'] };
// Preferred filename fragments (matches this ticket's own seed-file naming — DESIGN §8.6-8.10) tried
// before falling back to "first non-test source file of the right extension".
const PREFERRED_NAME_HINTS = ['lib/format', 'format.ts', 'views.py', 'handlers.go', 'server.go', 'lib.rs', 'handlers.rs', 'LengthClassifier.java'];
const EXCLUDE_DIRS = new Set(['node_modules', '.git', '.next', 'target', 'dist', 'build', '.venv', 'coverage', '.turbo', '.gradle']);
const TEST_MARKERS = ['.test.', '.spec.', '_test.go', 'test_', 'tests/', '/tests/', 'migrations/'];

function walk(dir, out) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    if (EXCLUDE_DIRS.has(e.name)) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else out.push(full);
  }
}

function pickMutationTarget(exts) {
  const all = [];
  walk(repoDir, all);
  const candidates = all.filter((f) => exts.includes(path.extname(f)) && !TEST_MARKERS.some((m) => f.includes(m)));
  if (candidates.length === 0) return null;
  for (const hint of PREFERRED_NAME_HINTS) {
    const hit = candidates.find((f) => f.replace(/\\/g, '/').includes(hint));
    if (hit) return hit;
  }
  // fallback: smallest file (cheapest to reason about / least likely to be a huge generated file)
  candidates.sort((a, b) => fs.statSync(a).size - fs.statSync(b).size);
  return candidates[0];
}

// Mutates a full temp COPY of the repo (fs.cpSync, unfiltered — node_modules/target/.venv must come
// along so the gate command actually has its deps to run), reruns the gate, expects nonzero, then
// discards the whole copy. The original repo-dir is never touched, so "restore" is simply deleting the
// copy — this is what the self-test (test-checker.mjs) verifies via `git status --porcelain` on the
// ORIGINAL repo-dir being empty after scoring.
function teethProbeSafe(gateName, points) {
  const info = gatesInput[gateName];
  if (info.mode === 'missing' || !info.cmd) return probe('teeth', gateName, points, false, 'gate not present, skipped');
  const exts = EXT_BY_STACK[stack] || [];
  const target = pickMutationTarget(exts);
  if (!target) return probe('teeth', gateName, points, false, 'no mutation target file found');
  const ext = path.extname(target);
  const snippet = (MUTATIONS[ext] || {})[gateName];
  if (!snippet) return probe('teeth', gateName, points, false, `no mutation snippet for ${ext}/${gateName}`);

  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'guardrails-teeth-'));
  try {
    fs.cpSync(repoDir, tmpRoot, { recursive: true });
    const rel = path.relative(repoDir, target);
    const copyTarget = path.join(tmpRoot, rel);
    fs.appendFileSync(copyTarget, snippet);
    const res = run(info.cmd, tmpRoot, 240000);
    const failedAsExpected = res.status !== 0 && res.status !== null;
    return probe('teeth', gateName, points, failedAsExpected, `mutated ${rel}, rerun exit=${res.status}`);
  } catch (e) {
    return probe('teeth', gateName, points, false, `error: ${e.message}`);
  } finally {
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }
}

// Coverage teeth: appending a small uncovered function is NOT a valid probe — on a repo sitting at
// ~100% coverage with an 85% floor it legitimately doesn't trip the gate (found 2026-07-03: run 2
// had 100/100/100/100 and "failed" the old probe). The honest regression is DELETING a test file:
// if the coverage gate still passes with a test file gone, the thresholds are decorative.
function teethCoverageProbe(points) {
  const info = gatesInput.coverage;
  if (info.mode === 'missing' || !info.cmd) return probe('teeth', 'coverage', points, false, 'gate not present, skipped');
  const all = [];
  walk(repoDir, all);
  const testFiles = all.filter((f) => TEST_MARKERS.some((m) => f.includes(m)) && /\.(ts|tsx|py|go|rs|java)$/.test(f));
  if (testFiles.length === 0) return probe('teeth', 'coverage', points, false, 'no test files found to delete');
  // delete the LARGEST test file — the biggest honest regression available in-repo
  testFiles.sort((a, b) => fs.statSync(b).size - fs.statSync(a).size);
  const target = testFiles[0];
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'guardrails-teeth-'));
  try {
    fs.cpSync(repoDir, tmpRoot, { recursive: true });
    const rel = path.relative(repoDir, target);
    fs.rmSync(path.join(tmpRoot, rel), { force: true });
    const res = run(info.cmd, tmpRoot, 240000);
    const failedAsExpected = res.status !== 0 && res.status !== null;
    return probe('teeth', 'coverage', points, failedAsExpected, `deleted ${rel}, rerun exit=${res.status}`);
  } catch (e) {
    return probe('teeth', 'coverage', points, false, `error: ${e.message}`);
  } finally {
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }
}

teethCoverageProbe(6);
teethProbeSafe('lint', 5);
teethProbeSafe('typecheck', 4);

// ============================================================================
// Cat 3 — Static-analysis maximalism config (12)
// ============================================================================
function cat3Next() {
  const eslintCfg = exists('eslint.config.mjs') ? 'eslint.config.mjs' : (exists('eslint.config.js') ? 'eslint.config.js' : null);
  const eslintText = eslintCfg ? readFileSafe(path.join(repoDir, eslintCfg)) : null;
  const maximal = grepAny(eslintText, ['sonarjs', 'eslint-plugin-unicorn', 'unicorn']);
  probe('static', 'maximal_ruleset', 4, maximal, `eslint config=${eslintCfg}`);

  const complexity = grepAny(eslintText, [/cognitive-complexity["'\s:,]+["']?error["']?[\s,\]]*15/, /"sonarjs\/cognitive-complexity"\s*:\s*\[\s*"error"\s*,\s*15/, /cognitive-complexity.{0,20}15/s]);
  probe('static', 'complexity_15', 3, complexity, 'sonarjs/cognitive-complexity threshold 15');

  const tsconfig = readJsonSafe(path.join(repoDir, 'tsconfig.json'));
  const strict = !!(tsconfig && tsconfig.compilerOptions && tsconfig.compilerOptions.strict === true);
  probe('static', 'strict_types', 3, strict, 'tsconfig.json compilerOptions.strict');

  const pkg = readJsonSafe(path.join(repoDir, 'package.json')) || {};
  const lintScript = (pkg.scripts || {}).lint || '';
  const noDefang = !grepAny(lintScript, ['--exit-zero', '--issues-exit-code=0']) && lintScript.includes('--max-warnings=0');
  probe('static', 'no_defang', 2, noDefang, `lint script: ${lintScript}`);
}

function cat3Django() {
  const pyproject = readFileSafe(path.join(repoDir, 'pyproject.toml'));
  const maximal = grepAny(pyproject, [/select\s*=\s*\[\s*"ALL"\s*\]/]);
  probe('static', 'maximal_ruleset', 4, maximal, 'ruff [tool.ruff.lint] select=["ALL"]');
  const complexity = grepAny(pyproject, [/max-complexity\s*=\s*15/]);
  probe('static', 'complexity_15', 3, complexity, 'ruff C901 max-complexity=15');
  // section-scoped: capture the whole [tool.mypy] body up to the NEXT [section] header at line
  // start — the old [^[]* halted at any "[" byte (e.g. inside `plugins = [...]`), missing
  // strict=true placed after it (found empirically by the tribunal Utility verifier, 2026-07-06).
  const mypySection = (pyproject || '').match(/\[tool\.mypy\]([\s\S]*?)(?=\n\[|$)/);
  const strict = !!mypySection && /(^|\n)\s*strict\s*=\s*true/i.test(mypySection[1]);
  probe('static', 'strict_types', 3, strict, '[tool.mypy] strict=true');
  const noDefang = !grepAny(pyproject, ['--exit-zero']);
  probe('static', 'no_defang', 2, noDefang, 'ruff/mypy strict-by-construction, no --exit-zero');
}

function cat3Go() {
  const golangci = readFileSafe(path.join(repoDir, '.golangci.yml')) || readFileSafe(path.join(repoDir, '.golangci.yaml'));
  const maximal = grepAny(golangci, [/version\s*:\s*"?2"?/]);
  probe('static', 'maximal_ruleset', 4, maximal, '.golangci.yml version: 2, curated-broad linters');
  const complexity = grepAny(golangci, [/gocognit/, /min-complexity\s*:\s*15/]);
  probe('static', 'complexity_15', 3, complexity, 'gocognit min-complexity: 15');
  const strict = exists('justfile') && grepAny(readFileSafe(path.join(repoDir, 'justfile')), ['go vet']);
  probe('static', 'strict_types', 3, strict, 'typecheck recipe = go vet ./...');
  const noDefang = !grepAny(golangci, ['issues-exit-code']) && !grepAny(readFileSafe(path.join(repoDir, 'justfile')), ['--issues-exit-code=0', '--exit-zero']);
  probe('static', 'no_defang', 2, noDefang, 'no --issues-exit-code=0 / --exit-zero');
}

function cat3Rust() {
  const cargoToml = readFileSafe(path.join(repoDir, 'Cargo.toml'));
  const maximal = grepAny(cargoToml, [/\[lints\.clippy\]/]) && grepAny(cargoToml, ['all', 'pedantic', 'nursery']);
  probe('static', 'maximal_ruleset', 4, maximal, 'Cargo.toml [lints.clippy] all+pedantic+nursery');
  const clippyToml = readFileSafe(path.join(repoDir, 'clippy.toml'));
  const complexity = grepAny(clippyToml, [/cognitive-complexity-threshold\s*=\s*15/]);
  probe('static', 'complexity_15', 3, complexity, 'clippy.toml cognitive-complexity-threshold=15');
  const toolchain = readFileSafe(path.join(repoDir, 'rust-toolchain.toml'));
  const strict = !!toolchain; // cargo check --all-targets is always "strict" typecheck; presence of pinned toolchain is the config signal
  probe('static', 'strict_types', 3, strict, 'rust-toolchain.toml pinned channel');
  const cargoConfig = readFileSafe(path.join(repoDir, '.cargo', 'config.toml'));
  const noDefang = grepAny(cargoConfig, [/lint\s*=\s*"[^"]*-D warnings/]);
  probe('static', 'no_defang', 2, noDefang, '.cargo/config.toml [alias] lint carries -D warnings');
}

function cat3Springboot() {
  const gradle = readFileSafe(path.join(repoDir, 'build.gradle')) || readFileSafe(path.join(repoDir, 'build.gradle.kts')) || '';
  const pmdRuleset = readFileSafe(path.join(repoDir, 'config', 'pmd', 'ruleset.xml'));
  const maximal = grepAny(gradle, ['checkstyle']) && grepAny(gradle, ['pmd']) && !!pmdRuleset;
  probe('static', 'maximal_ruleset', 4, maximal, 'checkstyle + pmd plugins applied, config/pmd/ruleset.xml present');
  const complexity = grepAny(pmdRuleset, [/CognitiveComplexity/]) && grepAny(pmdRuleset, [/reportLevel["'\s]*=?["'\s]*15/, /value\s*=\s*"15"/]);
  probe('static', 'complexity_15', 3, complexity, 'PMD CognitiveComplexity reportLevel=15');
  const strict = grepAny(gradle, [/-Xlint:all/]) && grepAny(gradle, ['-Werror']) && grepAny(gradle, ['errorprone']);
  probe('static', 'strict_types', 3, strict, '-Xlint:all -Werror + net.ltgt.errorprone on JavaCompile');
  const noDefang = !grepAny(gradle, ['ignoreFailures = true', 'ignoreFailures=true']) && !grepAny(gradle, ['issues-exit-code']);
  probe('static', 'no_defang', 2, noDefang, 'no ignoreFailures=true on checkstyle/pmd/jacocoTestCoverageVerification');
}

if (stack === 'next' || stack === 'nest') cat3Next();
else if (stack === 'django') cat3Django();
else if (stack === 'go') cat3Go();
else if (stack === 'rust') cat3Rust();
else if (stack === 'springboot') cat3Springboot();

// ============================================================================
// Cat 4 — Supply chain (8)
// ============================================================================
const tracked = gitLsFiles(repoDir);
const lockfileByStack = { next: 'package-lock.json', nest: 'package-lock.json', django: 'uv.lock', go: 'go.sum', rust: 'Cargo.lock', springboot: 'gradle.lockfile' };
const toolchainByStack = { next: '.nvmrc', nest: '.nvmrc', django: '.python-version', go: 'go.mod', rust: 'rust-toolchain.toml', springboot: '.sdkmanrc' };

probe('supply', 'audit_configured', 3, gatesInput.audit.mode !== 'missing', `audit gate mode=${gatesInput.audit.mode}`);
probe('supply', 'lockfile_committed', 2, tracked.includes(lockfileByStack[stack]), `expected ${lockfileByStack[stack]}`);
const toolchainFile = toolchainByStack[stack];
let toolchainPinned = exists(toolchainFile);
if (stack === 'go' && toolchainPinned) toolchainPinned = grepAny(readFileSafe(path.join(repoDir, 'go.mod')), [/^toolchain\s+go/m]);
probe('supply', 'toolchain_pinned', 3, toolchainPinned, `expected ${toolchainFile}`);

// ============================================================================
// Cat 5 — CI + Sonar (10)
// ============================================================================
function findWorkflows() {
  const dir = path.join(repoDir, '.github', 'workflows');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((f) => f.endsWith('.yml') || f.endsWith('.yaml')).map((f) => readFileSafe(path.join(dir, f)));
}
const workflows = findWorkflows();
const workflowText = workflows.join('\n---\n');
const ciRunsGates = workflows.length > 0 && GATE_NAMES.every((g) => workflowText.includes(g));
probe('ci', 'ci_runs_gates', 4, ciRunsGates, `${workflows.length} workflow file(s), all 7 gate names present`);
const shaLines = (workflowText.match(/uses:\s*\S+/g) || []);
// per-LINE check: each uses: must be SHA-pinned or carry its own `# TODO(pin)` annotation —
// a global grep let one TODO anywhere bless every line (Copilot review, PR #14).
const usesFullLines = workflowText.split('\n').filter((l) => /^\s*(-\s*)?uses:\s*\S+/.test(l));
const shaPinned = usesFullLines.length > 0 && usesFullLines.every((l) => /@[0-9a-f]{40}(\s|$)/.test(l) || /# TODO\(pin\)/.test(l));
const hardened = shaPinned && workflowText.includes('contents: read') && workflowText.includes('concurrency');
probe('ci', 'ci_hardened', 3, hardened, `uses: lines=${shaLines.length}`);
const sonarWired = grepAny(workflowText, ['sonarqube-scan-action', 'sonarqube-quality-gate-action']) && workflowText.includes('SONAR_ENABLED');
probe('ci', 'sonar_wired', 3, sonarWired, 'scan+quality-gate steps, SONAR_ENABLED-gated');

// ============================================================================
// Cat 6 — Hooks (5)
// ============================================================================
function cat6() {
  if (stack === 'next' || stack === 'nest') {
    const hookFile = readFileSafe(path.join(repoDir, '.husky', 'pre-commit'));
    // canon trio = typecheck + lint-staged (+ test-staged): require BOTH signal classes,
    // not either (Copilot review, PR #14 — lint-staged-only hooks must not fully score).
    const present = !!hookFile && grepAny(hookFile, ['lint']) && grepAny(hookFile, ['tsc', 'typecheck', 'test']);
    probe('hooks', 'hook_present', 3, present, '.husky/pre-commit');
    const pkg = readJsonSafe(path.join(repoDir, 'package.json')) || {};
    probe('hooks', 'hook_install_wired', 2, (pkg.scripts || {}).prepare === 'husky', 'package.json scripts.prepare=="husky"');
  } else if (stack === 'django') {
    const cfg = readFileSafe(path.join(repoDir, '.pre-commit-config.yaml'));
    probe('hooks', 'hook_present', 3, !!cfg && cfg.includes('ruff') && cfg.includes('mypy'), '.pre-commit-config.yaml has BOTH ruff and mypy (Copilot review, PR #14)');
    const doc = (readFileSafe(path.join(repoDir, 'AGENTS.md')) || '') + (readFileSafe(path.join(repoDir, 'README.md')) || '');
    probe('hooks', 'hook_install_wired', 2, doc.includes('pre-commit install'), 'documented `pre-commit install` step');
  } else {
    const cfg = readFileSafe(path.join(repoDir, 'lefthook.yml'));
    // canon lefthook = lint-class + format-class commands (go: golangci-lint+gofmt;
    // springboot: checkstyle/pmd+spotless; rust: clippy+fmt) — require both classes
    // (Copilot review, PR #14).
    const present = !!cfg && grepAny(cfg, ['lint', 'clippy']) && grepAny(cfg, ['fmt', 'format', 'spotless']);
    probe('hooks', 'hook_present', 3, present, 'lefthook.yml has lint-class AND format-class commands');
    const doc = (readFileSafe(path.join(repoDir, 'AGENTS.md')) || '') + (readFileSafe(path.join(repoDir, 'README.md')) || '');
    probe('hooks', 'hook_install_wired', 2, doc.includes('lefthook install'), 'documented `lefthook install` step');
  }
}
cat6();

// ============================================================================
// Cat 7 — Agent surface (5)
// ============================================================================
const agentsMd = readFileSafe(path.join(repoDir, 'AGENTS.md'));
const agentsMdOk = !!agentsMd && GATE_NAMES.every((g) => agentsMd.includes(g));
probe('agent', 'agents_md', 3, agentsMdOk, 'AGENTS.md present with all 7 gate names');
const claudeMd = readFileSafe(path.join(repoDir, 'CLAUDE.md'));
probe('agent', 'claude_import', 1, !!claudeMd && claudeMd.includes('@AGENTS.md'), 'CLAUDE.md contains @AGENTS.md');
// NOTE: never credit .claude/skills or .agents/skills directory PRESENCE — the benchmark harness
// itself pre-creates .claude/skills in both arms (run-arm.sh), so a presence probe would score a
// harness artifact, not agent work. skills-lock.json is the pin artifact `npx skills add` writes;
// only agent work produces it. (Defect found 2026-07-03 on bare-sonnet-go-go-1; fixed.)
const skillsLock = exists('skills-lock.json');
probe('agent', 'skills_lock', 1, skillsLock, 'skills-lock.json present (npx skills add pin artifact)');

// ============================================================================
// Cat 8 — Git hygiene (5)
// ============================================================================
function gitStatusPorcelain(dir) {
  const res = spawnSync('git', ['status', '--porcelain'], { cwd: dir, encoding: 'utf8' });
  return res.status === 0 ? res.stdout : null;
}
function gitHasCommit(dir) {
  const res = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: dir, encoding: 'utf8' });
  return res.status === 0;
}
const porcelain = gitStatusPorcelain(repoDir);
const committedClean = gitHasCommit(repoDir) && porcelain !== null && porcelain.trim() === '';
probe('hygiene', 'committed_clean', 3, committedClean, `porcelain empty=${porcelain !== null && porcelain.trim() === ''}`);
const artifactDirs = ['node_modules', 'target', '.venv', '.next', 'coverage', '.gradle', 'build'];
const hasArtifacts = artifactDirs.some((d) => tracked.some((f) => f.startsWith(`${d}/`) || f === d));
const gitignore = readFileSafe(path.join(repoDir, '.gitignore'));
const noArtifacts = !hasArtifacts && !!gitignore;
probe('hygiene', 'no_artifacts', 2, noArtifacts, `.gitignore present=${!!gitignore}, tracked artifact dirs=${hasArtifacts}`);

// ============================================================================
// Totals
// ============================================================================
// Recompute category subtotals from the emitted METRIC lines rather than tracking N running counters —
// single source of truth, impossible to let a subtotal drift from its own probe lines.
const byCat = {};
for (const l of lines) {
  const m = l.match(/^METRIC (\w+)__\w+=(-?\d+(?:\.\d+)?)$/);
  if (!m) continue;
  byCat[m[1]] = (byCat[m[1]] || 0) + Number(m[2]);
}
const catLabel = { gate: 'cat_executable_gates', teeth: 'cat_teeth', static: 'cat_static_analysis', supply: 'cat_supply_chain', ci: 'cat_ci_sonar', hooks: 'cat_hooks', agent: 'cat_agent_surface', hygiene: 'cat_git_hygiene' };
for (const [k, label] of Object.entries(catLabel)) metric(label, byCat[k] || 0);

const rawTotal = Object.values(byCat).reduce((a, b) => a + b, 0);
const cappedTotal = allGatesPass ? rawTotal : Math.min(rawTotal, 50);

// ---------- emit ----------
for (const l of lines) console.log(l);
console.log(`METRIC raw_score=${rawTotal}`);
console.log(`METRIC all_gates_pass=${allGatesPass ? 1 : 0}`);
console.log(`METRIC guardrail_score=${cappedTotal}`);

console.error(`\n# TOTAL raw=${rawTotal} all_gates_pass=${allGatesPass} guardrail_score=${cappedTotal}`);
