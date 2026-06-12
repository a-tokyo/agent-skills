# tribunal

Doer → verifier-panel → consensus: a delivery-verification pattern for orchestrating
agents. An orchestrator freezes acceptance criteria before implementation, dispatches a
doer, then convenes a context-walled panel of independent verifiers — including an
adversary with an explicit must-oppose mandate — for evidence-anchored review
adjudicated to a SHIP / SHIP_WITH_CAVEATS / ITERATE / BLOCK / ESCALATE verdict.

Start at [SKILL.md](SKILL.md). Mechanics live in [references/](references/): consensus
mechanics (triggers, synthesis, resolution math, adjudication), a worked end-to-end
example, and an anti-pattern catalogue. The skill is principles-first: panel size,
lenses, scoring dimensions, prompts, and record shapes are derived per artifact from
stated invariants rather than prescribed.

Works with any agent platform that can spawn parallel subagents; degrades to
sequential fresh-context sessions (with reduced independence, labeled as such)
when it cannot.

## Benchmarks (v0.1.2)

Method: identical neutral prompts per arm, the only variable being which skill version
(if any) was installed. Reports blind-judged (anonymized candidates, randomized order,
answer key withheld from all arms); outcome scores computed against a private answer
key whose failure scenarios were executed, not asserted. Scoring is outcome-weighted
(80/20) and process credit is restricted to outcome-linked behaviors — a single-pass
report with the same findings scores identical process points to a panel report, so
the skill cannot earn points for its own ceremony.

**Generation-2 fixture:** a ~560-line metering/billing module, 12 seeded defects in
three difficulty tiers (tier-weighted ×1/×2/×3), 3 non-defect traps, expected verdict
ITERATE.

| Arm | Composite | Tier-weighted recall | Trap FPs | Other FPs | Verdict |
|---|---|---|---|---|---|
| **this version (135-line body)** | **9.65** | 26/26 | 0 | 0 | ITERATE (correct) |
| prior prescriptive version (208-line body) | 9.65 | 26/26 | 0 | 0 | ITERATE (correct) |
| frontier-tier model, no skill | 9.45 | 26/26 | 0 | 1 | ITERATE (correct) |
| earlier simplification drafts (two) | 8.65–9.00 | 26/26 | 0 | 1–2 | BLOCK (miscalibrated) |

Honest readings, including the negative ones:

- **A frontier-tier model alone is near parity on artifacts of this size** (9.45 vs
  9.65): recall saturated at 26/26 in every arm, including no-skill. The skill's
  measured value is precision (the no-skill arm filed a false positive against
  spec-compliant behavior) and verdict calibration — not raw defect-finding.
- **Simplification only matched the prescriptive version after two failed attempts.**
  The first philosophy-only draft fabricated evidence quotes and over-blocked; fixing
  it required restoring three prescriptive guardrails (verbatim-quote grepping before
  consensus math, named adversary attack vectors, and an explicit
  BLOCK-is-structural rule). Principles alone did not preserve behavior; principles
  plus a few load-bearing guardrails did — at one third the token footprint.
- The benchmarking surfaced a latent spec bug present since the first version: a
  "majority scores a dimension ≤3 → BLOCK" rule that mechanically forces BLOCK on any
  defect-dense artifact even when every fix is localized. This version redefines BLOCK
  as structural (redesign-or-unsafe); defect-dense-but-localized is ITERATE with a
  mandatory fix list.
- From generation-1 benchmarks (easier fixture, both implement-and-verify and
  seeded-defect tasks): the with-skill arm caught and escalated a genuine spec
  contradiction the no-skill arm shipped silently, and hardened edge paths the
  single review pass missed; a mid-tier model was NOT lifted to frontier-tier level
  by the skill (it simulated the panel in one context — the degraded mode this skill
  labels as weaker).

Full judge reports, fixtures, answer keys, and the iteration ledger live in the build
workspace of the skill factory that produced this skill.

## License

MIT
