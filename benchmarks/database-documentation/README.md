# database-documentation — benchmark

Measures whether the `database-documentation` skill makes an AI coding agent produce database documentation that is
**exactly** the schema — not a plausible approximation. The metric is parity against the live database, and
the headline is binary: did the docs reach **exact 100% parity (zero defects)**, which is the skill's
promise, or not.

## Method

```mermaid
flowchart LR
  DB[(live database)] -->|deterministic catalog read| ORACLE[ground-truth extractor<br/>extract-pg / extract-mssql]
  REPO[repo + live DB] -->|agent applies skill OR bare prompt| AGENT[fresh agent]
  AGENT -->|schema.json| SCORER
  ORACLE -->|canonical IR| SCORER{parity scorer}
  SCORER --> M[exact_parity · total_defects · per-class F1]
```

- **Oracle (ground truth).** A deterministic extractor reads the engine catalog (`pg_catalog` / `sys.*`) and
  emits a Canonical Schema Model. Code cannot hallucinate or omit; the same DB always yields byte-identical
  output (determinism gate: extract twice, diff). This — not an LLM judge — is the source of truth.
- **A/B arms.** A fresh agent documents the database either **with** the skill or with a **bare** prompt
  (same output contract, same DB access). Only the skill's methodology differs.
- **Scorer.** Parses the agent's `schema.json` into the same canonical IR and diffs per object class
  (tables, columns + type/nullable/default, PKs, FKs + on_delete, uniques, checks, indexes incl
  partial/expression, enums + values, views, triggers, routines, sequences). It is **substance-normalized**
  (identifier matching is case-insensitive but underscore-sensitive, so a wrong physical name like
  `client_id` for a `clientId` column is still flagged) and **dialect-fair** (default paren-wrapping,
  `on_delete` spelling, temporal default scale), so the score reflects schema substance, not naming/dialect
  style. Primary signals: `exact_parity` (1 iff zero defects), `total_defects` (missing + hallucinated +
  attribute mismatches), and per-class F1.

## Targets

- **Public fixture** (`fixture/`, reproducible by anyone): a small schema rendered in both PostgreSQL and
  SQL Server, containing one of every object class (native + CHECK + app-level enums, composite PK, FKs with
  CASCADE/RESTRICT/SET NULL, partial/expression/GIN + filtered indexes, generated/computed column, JSON,
  citext/CI collation, view, trigger, function, sequence, comments, extension functions to exclude).
  `docker compose up` and run — no private code needed.
- **Private real repos** (maintainer-run; results committed, code not shipped): a 211-table PostgreSQL +
  TypeORM app, and a 54-table SQL Server + Prisma app.

## Results (medians; `exact_parity` / `total_defects`)

| target | engine / ORM | model | with skill | bare baseline |
|---|---|---|---|---|
| real app A | PostgreSQL / TypeORM | Opus | **1 / 0** | 0 / 81 |
| real app A | PostgreSQL / TypeORM | Sonnet | **1 / 0** | did-not-finish (unbounded, no output) |
| real app A | PostgreSQL / TypeORM | Haiku | **1 / 0** | 0 / 2182 |
| real app B | SQL Server / Prisma | Opus | **1 / 0** | 0 / 338 |
| public fixture | PostgreSQL | Opus | **1 / 0** | 0 / 7 |
| public fixture | SQL Server | Opus | **1 / 0** | — |

(`exact_parity / total_defects`, scored by the strengthened scorer that routes every schema-object class —
including partial-index predicates, index method, trigger/view bodies, FK on-update, routines (name + kind),
and column flags — into the diff. An earlier, weaker scorer had let some of these slip; the audit that hardened
it also re-confirmed every "with skill" cell stays at exact `1 / 0`.)

**Read this honestly:**
- The skill drives **exact 100% parity at every model tier on both engines**, including held-out targets it
  never trained on. No bare arm reaches exact 100% on a real app.
- The **bare-model gap widens sharply as the model weakens**: a strong model (Opus) misses ~1 object; a weak
  model (Haiku) defaults to parsing the ORM and ships **2182 defects** — incomplete (missing 58 tables, 541
  columns, all sequences/triggers/checks) *and* hallucinated (phantom enums, a typo'd table). A mid model
  (Sonnet) without the skill explored unboundedly and produced nothing.
- Even on the **small public fixture**, the bare Opus baseline misses **7** schema facts (a trigger body,
  a view body, the computed-column expression, an FK on-update, index methods, etc.) — things a strong model
  skims past but the skill's verification loop catches. The fixture has no application repo, so there is no
  ORM to mis-read (the skill's single biggest lever, grounding-vs-ORM, is removed); even so the gap persists.
  On real repos with an ORM present and weaker models, that gap explodes (Haiku: 2182).
- Where a strong model already does well, the skill's residual value is **the verification loop that closes
  the last mile to exactly zero defects**, **precision** (it does not invent app-level enums as DB objects),
  and **hard-case correctness** (CHECK-enums, partial/expression indexes, extension-object exclusion, drift).

## Reproduce (public fixture)

```bash
cd fixture && docker compose up -d                 # Postgres :5440, SQL Server :1434
# document with the skill (fresh agent), then:
node ../evaluation/extract-pg.mjs  > truth.json    # DATABASE_URL=postgres://bench:bench@localhost:5440/bench
node ../evaluation/score.mjs truth.json <agent-schema.json>   # -> METRIC exact_parity / total_defects
```

The scorer ships its own self-tests: empty docs score 0.0, the oracle round-tripped as a candidate scores
1.0, and the oracle is asserted byte-identical across two extractions before any run.
