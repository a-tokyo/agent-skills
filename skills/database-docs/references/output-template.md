# Output template — the human doc shape

Render these from the CSM. Use **physical DB identifiers** throughout. Pick the layout by table count.

## Small schema (≤ ~15 tables): single `DATABASE.md`

```markdown
# <Database> schema

> Engine: PostgreSQL 16.8 · 12 tables · 3 enums · Generated <date> · Parity: verified (0 undocumented, 0 invented)

## ER diagram
\`\`\`mermaid
erDiagram
  user { int id PK; varchar email; int client_id FK }
  client { int id PK; varchar name }
  user }o--|| client : client_id
\`\`\`

## Tables

### user
Central user entity for all system users.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| id | integer | NO | nextval(...) | Primary key |
| email | varchar(255) | NO | | Login email |
| client_id | integer | YES | | Owning client |

**Indexes:** `idx_user_email` on (email)
**Foreign Keys:** `client_id` → `client(id)` ON DELETE SET NULL

## Issues / drift
- ...
```

## Large schema: multi-file under `docs/db/`

```
docs/db/
  README.md                 # index + stats + Parity Report
  ONBOARDING.md             # narrative intro for newcomers
  schema.json               # the CSM (machine-readable source of truth)
  diagrams/
    <domain>.md             # per-domain: mermaid erDiagram (all cols+types) + per-table sections
    full-erd.md             # table-level boxes + relationships only (stays renderable at 200+ tables)
  tables/
    entity-reference.md     # master index of every table + one-line description
  issues/
    README.md               # drift findings (as RCA: symptom → root cause → fix) + tech-debt
```

Per-domain file = a `mermaid erDiagram` block (every table with all columns + types, PK/FK markers, then
relationship lines like `User }o--|| Client : belongs_to`), followed by the same per-table sections shown
above (description + `Column|Type|Nullable|Default|Description` table + **Indexes:** + **Foreign Keys:**).

## Domain partitioning (large schemas)

Choose domains by, in order of preference: (1) ORM module/folder grouping; (2) shared table-name prefix
(`my_lqa_*`, `post_assessment_*`); (3) foreign-key clusters (connected components of the FK graph). Put
weakly-connected leftovers in a `Legacy/Misc` domain. Cap a domain's ERD at ~20–25 tables for readability.

## Confidence / unverified marking (tiers T3–T5)

When the docs were NOT verified against a live database (degradation tiers T3–T5), the reader must never
mistake them for verified truth. Put a prominent banner at the top of **every** output file (not just the
README), e.g. `> ⚠️ UNVERIFIED — generated from migrations/ORM consensus, not checked against a live database.`
If individual objects carry a `confidence` below `verified` in the CSM, mark them inline in their per-table
section (e.g. a `confidence: consensus` note), so a reader landing mid-document sees the caveat. A verified
(T1/T2) doc carries the Parity Report instead and needs no per-object marking.

## Descriptions

Derive from, in order: DB column/table comments → ORM field comments → seed-data semantics → careful
inference from name + usage. Mark inferred descriptions as such; the adversary review challenges invented
prose. Never invent a description that asserts behavior you did not verify.
