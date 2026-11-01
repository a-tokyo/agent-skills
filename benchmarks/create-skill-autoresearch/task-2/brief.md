# Build brief: `purchase-order-builder`

This brief answers every factory interview question. Do not ask the user anything —
work non-interactively from this document.

## Purpose (1.1)

Build a skill named `purchase-order-builder` that turns a free-text **purchase request**
(`request.md`) into a **purchase order JSON** (`po.json`) by looking items, vendors, cost centers,
sites and exchange rates up in a folder of **master-data CSV files** (`master-data/`).

- Domain: procurement back-office — matching requested items to a catalog and applying the
  team's purchasing rules
- Target user: an AI agent with a shell and Python 3, asked to process one request at a time
- Success: `po.json` matches what the procurement team would have produced — same SKUs, vendors,
  quantities, EUR prices, totals, approval tier, review flag and exception codes

## Gold standards (1.2)

Seven pairs in `input/`: `request-01.md`..`request-07.md` with the purchase orders the team
produced for them, `po-01.json`..`po-07.json`, all against the master data in
`input/master-data/`. The team's purchasing rules are only partly written down — the gold
standards are the primary source for the rest. Study them closely.

## Study materials (1.3)

- `materials/policy.md` — the written part of the purchasing policy, the request format, the
  master-data columns and the output schema. It is two years old; where past orders disagree with
  it, past orders are right.
- The gold standards above

## Scope and constraints (1.4)

- **Master data is replaced every month.** The skill must work on master-data folders it has
  never seen (different SKUs, prices, vendors, codes and currencies). Never hard-code their
  contents.
- **Production catalogs hold tens of thousands of rows.** Do not read whole CSVs into the
  conversation. Deterministic work (finding rows, filtering, converting, rounding, totalling)
  belongs in small bundled script functions; the model's job is reading the request and
  orchestrating. No single "do everything" function either.
- SKILL.md body under **300 lines**; bundle scripts under `scripts/`; `references/` only if needed
- No integration skills
- Invocation mode: **model-invoked** — the agent should pick it up when asked to turn a purchase
  request into a purchase order
- Evaluation budget, if applicable: `target_score: 0.85`, `max_iterations: 6`

## Existing skill (1.5)

None. Greenfield.

## Output location

Write the finished skill to `output/purchase-order-builder/` (SKILL.md, `scripts/`, and
`references/` only if needed) inside the current working directory. When the skill runs, the
agent's working directory contains `request.md` and `master-data/`, and it must write `po.json`.
