# Build brief: `freight-quote-builder`

This brief answers every factory interview question. Do not ask the user anything —
work non-interactively from this document.

## Purpose (1.1)

Build a skill named `freight-quote-builder` that turns a free-text **shipment request**
(`shipment.md`) into a **parcel quote JSON** (`quote.json`) by rating it against a folder of
**carrier rate tables** (`rates/`).

- Domain: logistics back-office — rating multi-box parcel shipments across carriers (volumetric and
  billable weight, zone rate bands, surcharges, fuel, currency) and choosing the carrier and service
- Target user: an AI agent with a shell and Python 3, asked to quote one shipment at a time
- Success: `quote.json` matches what the shipping desk would have produced — same carrier, service,
  billable weight, EUR base charge, surcharges, fuel, total, delivery date and exceptions

## Gold standards (1.2)

Eight pairs in `input/`: `shipment-01.md`..`shipment-08.md` with the desk-log entries for the quotes
the desk produced for them, `quote-01.json`..`quote-08.json`, all against the rate tables in
`input/rates/`. The desk log is a summary: it records the carrier, service, billable weight, which
surcharges applied, the total, the delivery date and any exceptions, but not the base charge, the
surcharge amounts or the fuel. The skill must still produce the full `quote.json` schema in the
guide, because the booking system needs every field. The desk's rating rules are only partly
written down — the gold standards are the primary source for the rest. Study them closely; several
rules only show when you redo a quote's arithmetic.

## Study materials (1.3)

- `materials/rating-guide.md` — the request format, the rate-table columns and the output schema
  (Part 1, current) and the written rating rules (Part 2, two years old). Where past quotes disagree
  with Part 2, past quotes are right. Where past quotes do not settle a question, the guide is right.
- The gold standards above

## Scope and constraints (1.4)

- **Rate tables are replaced every month.** The skill must work on rate folders it has never seen
  (different carriers, thresholds, surcharge units, fuel rates, currencies, zones, remote areas,
  holidays and weight breaks). Never hard-code their contents.
- **Production rate tables hold thousands of rows.** Do not read whole CSVs into the conversation.
  Deterministic work (unit conversion, weights, band lookups, surcharges, currency, dates, choosing
  the cheapest option) belongs in small bundled script functions; the model's job is reading the
  request and orchestrating. No single "do everything" function either.
- SKILL.md body under **300 lines**; bundle scripts under `scripts/`; `references/` only if needed
- No integration skills
- Invocation mode: **model-invoked** — the agent should pick it up when asked to quote a shipment or
  turn a shipment request into a freight quote
- Evaluation budget, if applicable: `target_score: 0.85`, `max_iterations: 6`

## Existing skill (1.5)

None. Greenfield.

## Output location

Write the finished skill to `output/freight-quote-builder/` (SKILL.md, `scripts/`, and
`references/` only if needed) inside the current working directory. When the skill runs, the
agent's working directory contains `shipment.md` and `rates/`, and it must write `quote.json`.
