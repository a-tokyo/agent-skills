#!/usr/bin/env node
// lib/config-present.mjs — "is the e2e config/spec present" fallback check.
//
// evaluate.sh uses this in place of actually EXECUTING the e2e gate when run without --e2e (no
// guaranteed network/browser availability offline). audit's fallback needs no file check: its "config"
// is the gate entry itself (already known to the caller from gate detection), so this module always
// reports audit as present — presence/absence is decided by evaluate.sh from the resolved gate command.
//
// Usage: node lib/config-present.mjs <repo-dir> <stack> <e2e|audit>   -> exit 0 (present) | 1 (absent)

import fs from 'node:fs';
import path from 'node:path';

const [repoDir, stack, gate] = process.argv.slice(2);
if (!repoDir || !stack || !gate) { console.error('usage: config-present.mjs <repo-dir> <stack> <e2e|audit>'); process.exit(2); }

function exists(...segs) { return fs.existsSync(path.join(repoDir, ...segs)); }

const EXCLUDE_DIRS = new Set(['node_modules', '.git', '.next', 'target', 'dist', 'build', '.venv', 'coverage', '.gradle']);
function grepTree(dir, pattern, exts) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return false; }
  for (const e of entries) {
    if (EXCLUDE_DIRS.has(e.name)) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) { if (grepTree(full, pattern, exts)) return true; continue; }
    if (!exts.includes(path.extname(e.name))) continue;
    let text;
    try { text = fs.readFileSync(full, 'utf8'); } catch { continue; }
    if (pattern.test(text)) return true;
  }
  return false;
}

function e2ePresent() {
  switch (stack) {
    case 'next':
      return fs.existsSync(repoDir) && fs.readdirSync(repoDir).some((f) => /^playwright\.config\.(ts|js|mjs)$/.test(f));
    case 'nest':
      return exists('vitest.config.e2e.ts') || exists('vitest.e2e.config.ts');
    case 'django':
      return grepTree(repoDir, /mark\.e2e|live_server/, ['.py']);
    case 'go':
      return grepTree(repoDir, /httptest\.NewServer/, ['.go']);
    case 'rust':
      return exists('tests', 'e2e.rs');
    case 'springboot':
      return grepTree(repoDir, /@Tag\(\s*"e2e"\s*\)|WebEnvironment\.RANDOM_PORT/, ['.java']);
    default:
      return false;
  }
}

const ok = gate === 'e2e' ? e2ePresent() : true;
process.exit(ok ? 0 : 1);
