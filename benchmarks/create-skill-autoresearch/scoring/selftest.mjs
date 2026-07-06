#!/usr/bin/env node
// selftest.mjs — offline unit tests for the scorer's probes. No network, no API key.
// A green run means the checks discriminate; run before trusting any benchmark score.

import { checkCommitMessage, checkCompliance, checkCraft, parseFirstLine } from "./score.mjs";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const KEY = JSON.parse(readFileSync(join(HERE, "..", "task", "holdout", "answer-key.json"), "utf8"));

let failures = 0;
function assert(name, cond, detail = "") {
  if (cond) console.log(`ok   ${name}`);
  else { console.log(`FAIL ${name} ${detail}`); failures++; }
}

// --- parseFirstLine ---
assert("parse plain", parseFirstLine("fix(web): clamp date range")?.type === "fix");
assert("parse scope", parseFirstLine("feat(api)!: drop v1")?.scope === "api");
assert("parse bang", parseFirstLine("feat(api)!: drop v1")?.bang === true);
assert("parse revert", parseFirstLine("revert: feat(api): coalesce cache reads")?.revert === true);
assert("parse garbage", parseFirstLine("Fixed the thing.") === null);
assert("parse bad type", parseFirstLine("feature(api): x") === null);

// --- checkCommitMessage: perfect answers score 1.0 ---
const perfectH1 = `feat(web)!: store session tokens in an httpOnly cookie

- store session tokens in an httpOnly cookie instead of localStorage
- remove the exported getToken() helper
- read the session from the cookie-backed store in AuthProvider

BREAKING CHANGE: getToken() is removed; sessions are no longer readable from localStorage.`;
const h1 = checkCommitMessage(perfectH1, KEY.h1);
assert("h1 perfect = 1.0", h1.score === 1, JSON.stringify(h1.checks));

const perfectH2 = `build(deps): bump axios from 1.6.0 to 1.7.2

- bump axios from 1.6.0 to 1.7.2
- refresh lockfile`;
const h2 = checkCommitMessage(perfectH2, KEY.h2);
assert("h2 perfect = 1.0", h2.score === 1, JSON.stringify(h2.checks));

const perfectH3 = `revert: feat(api): add retry-with-backoff to webhook delivery

This reverts commit 4e8d21c.`;
const h3 = checkCommitMessage(perfectH3, KEY.h3);
assert("h3 perfect = 1.0", h3.score === 1, JSON.stringify(h3.checks));

// --- checkCommitMessage: generic-skill answers lose the team-convention checks ---
const genericH1 = `refactor(web): Moved session tokens to cookies.`; // wrong style, no bang, no footer, no bullets
const g1 = checkCommitMessage(genericH1, KEY.h1);
assert("h1 generic <= 0.5", g1.score <= 0.5, `score=${g1.score}`);
assert("h1 generic fails breaking_bang", g1.checks.breaking_bang === false);
assert("h1 generic fails subject_style", g1.checks.subject_style === false);

const genericH2 = `chore: update dependencies`; // wrong type, wrong scope, no bullets, no keywords
const g2 = checkCommitMessage(genericH2, KEY.h2);
assert("h2 generic <= 0.5", g2.score <= 0.5, `score=${g2.score}`);

const genericH3 = `fix(api): remove webhook retry logic`; // not revert form, no sha reference
const g3 = checkCommitMessage(genericH3, KEY.h3);
assert("h3 generic fails type", g3.checks.type_match === false);
assert("h3 generic fails revert_body", g3.checks.revert_body === false);

// --- fence stripping ---
const fenced = "```\n" + perfectH2 + "\n```";
assert("fences stripped", checkCommitMessage(fenced, KEY.h2).score === 1);

// --- breaking must not be over-asserted ---
const overBreaking = `build(deps)!: bump axios from 1.6.0 to 1.7.2

- bump axios from 1.6.0 to 1.7.2

BREAKING CHANGE: none really`;
const ob = checkCommitMessage(overBreaking, KEY.h2);
assert("h2 spurious breaking penalized", ob.checks.breaking_bang === false && ob.checks.breaking_footer === false);

// --- compliance + craft on fixtures ---
const good = join(HERE, "fixtures", "good-skill");
const bad = join(HERE, "fixtures", "bad-skill");
const goodC = checkCompliance(good);
assert("good fixture compliance = 1.0", goodC.score === 1, JSON.stringify(goodC.checks));
const badC = checkCompliance(bad);
assert("bad fixture fails name_valid", badC.checks.name_valid === false);
assert("bad fixture fails user_invoked", badC.checks.user_invoked === false);
const goodK = checkCraft(good);
assert("good fixture craft = 1.0", goodK.score === 1, JSON.stringify(goodK.checks));
const badK = checkCraft(bad);
assert("bad fixture fails one-line description", badK.checks.description_one_line === false);
assert("bad fixture fails trigger scaffolding", badK.checks.no_trigger_scaffolding === false);
assert("bad fixture fails concrete example", badK.checks.concrete_example === false);

// --- missing skill dir ---
assert("missing dir scores 0", checkCompliance(join(HERE, "fixtures", "nope")).score === 0);

console.log(failures === 0 ? "\nSELFTEST PASS" : `\nSELFTEST FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
