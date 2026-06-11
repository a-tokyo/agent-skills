# tribunal

Doer → verifier-panel → consensus: a delivery-verification pattern for orchestrating
agents. An orchestrator slices the work, freezes acceptance criteria before
implementation, spawns a doer, then convenes a context-walled panel — Verifier-A
(Quality), Verifier-B (Utility), and a Devil's Advocate — for independent,
evidence-anchored review with adjudicated consensus and a SHIP / SHIP_WITH_CAVEATS /
ITERATE / BLOCK / ESCALATE verdict.

Start at [SKILL.md](SKILL.md). Mechanics live in [references/](references/):
consensus protocol, copy-paste role prompts, pre-implementation plan vetting, a
worked end-to-end example, and an anti-pattern catalogue.

Works with any agent platform that can spawn parallel subagents; degrades to
sequential fresh-context sessions (with reduced independence, labeled as such)
when it cannot.

## Benchmarks (v0.0.1)

Method: two fixed tasks, identical prompts per arm, the only variable being whether
this skill was installed. Reports were blind-judged (candidates anonymized, order
randomized, answer keys withheld from all arms) and outcome scores were computed
deterministically — the judge re-executed test suites and counted findings against
a private answer key rather than trusting reports.

**Task 1 — seeded-defect verification.** A ~360-line module with 10 seeded defects
(3 obvious / 4 moderate / 3 subtle) and 2 non-defect traps; expected verdict ITERATE.

| Arm | Composite | Recall | False positives | Verdict |
|---|---|---|---|---|
| frontier-tier + skill | **9.50** | 10/10 | 0 | ITERATE (correct) |
| frontier-tier, no skill | 8.10 | 10/10 | 0 | ITERATE (correct) |
| mid-tier + skill | 6.78 | 10/10 | 1 | BLOCK (miscalibrated) |
| mid-tier, no skill | 6.45 | 10/10 | 0 | BLOCK + rewrite call (miscalibrated) |

**Task 2 — implement-and-verify.** A 3-slice CLI spec with 17 acceptance criteria;
the judge re-ran every suite and probed edge cases beyond the tests.

| Arm | Composite | Outcome | Process |
|---|---|---|---|
| frontier-tier + skill | **9.65** | 10.0 (17/17 criteria, suite green) | 9.3 |
| frontier-tier, no skill | 8.35 | 10.0 (17/17 criteria, suite green) | 6.7 |

Honest readings, including the negative ones:

- The skill's measured value is **process rigor and verdict calibration**, not raw
  defect recall — recall saturated at 10/10 in every arm on Task 1 (the fixture was
  too easy on that axis; a harder fixture is queued for the next benchmark round).
- On Task 2 the outcomes tied; the separation came entirely from process: the
  with-skill arm caught a genuine spec contradiction, pinned it with a test, and
  escalated it, while the control shipped the same behavior silently. The with-skill
  arm also hardened paths (broken-pipe handling, calendar-date validation) that the
  control's single review pass missed.
- **The skill did not lift the mid-tier model to frontier-tier level on Task 1**
  (6.78 vs the frontier control's 8.10). The mid-tier arm simulated the panel inside
  one context instead of spawning real subagents — exactly the degraded mode the
  skill itself labels as weaker. Uplift over its own no-skill baseline was modest
  (+0.33), mostly verdict-reasoning quality.

Full judge reports, fixtures, answer keys, and the iteration ledger live in the
build workspace of the skill factory that produced this skill.

## License

MIT
