# Canonical Schema Model (CSM) — the `schema.json` contract

## Contents
- Purpose
- Top-level shape
- Table shape
- Conventions (the rules that make it verifiable)

## Purpose

`schema.json` is the machine-readable source of truth for the documentation. It is engine-agnostic: every
database (Postgres, MySQL, SQL Server, SQLite) and every ORM normalizes into this one shape. The human
markdown is rendered from it, and a drift/parity check diffs it against a fresh introspection of the live
database. Emit it alongside the human docs (e.g. `docs/db/schema.json`).

Every table name, column name, etc. uses the **physical database identifier** (the name the DB engine
reports), never the ORM class/property name. `user`, not `User`; `client_id`, not `clientId` — unless the
DB column is literally `clientId` (quoted camelCase, as some TypeORM setups produce), in which case use
exactly what the catalog reports.

## Top-level shape

```jsonc
{
  "meta": { "engine": "postgres|mysql|mssql|sqlite", "engine_version": "16.8", "schemas": ["public"] },
  "tables":   [ /* Table objects — see below; includes views/materialized views with kind set */ ],
  "enums":    [ { "schema": "public", "name": "score_type", "values": ["a","b","c"] } ],
  "domains":  [ { "schema": "public", "name": "email", "base_type": "text", "constraints": "CHECK ((VALUE ~ '...'))" } ],
  "sequences":[ { "schema": "public", "name": "user_id_seq" } ],
  "routines": [ { "schema": "public", "name": "calc_score", "kind": "function|procedure", "returns": "integer", "arguments": "p_id integer", "language": "plpgsql" } ]
}
```

## Table shape

```jsonc
{
  "schema": "public",
  "name": "user",
  "kind": "table|partitioned|foreign|view|materialized_view",
  "comment": "Central user entity." ,          // null if none
  "columns": [
    {
      "name": "user_email", "ordinal": 7,
      "type": "character varying(255)",          // full rendered type WITH length/precision/scale
      "nullable": true,
      "default": null,                            // verbatim default expression or null
      "is_identity": false, "is_generated": false,   // generated/computed column? put its expression in `default`
                                                      // (Postgres GENERATED ALWAYS AS / SQL Server computed `AS (...)`)
      "collation": "case_insensitive",            // citext/COLLATE if any, else null
      "comment": null,
      "enum_ref": null,                           // enum name if the column is enum-typed
      "domain_ref": null
    }
  ],
  "primary_key": { "name": "user_pkey", "columns": ["id"] },   // null if none
  "foreign_keys": [
    { "name": "fk_user_client", "columns": ["client_id"], "ref_schema": "public", "ref_table": "client",
      "ref_columns": ["id"], "on_delete": "CASCADE", "on_update": "NO ACTION", "def": "FOREIGN KEY ..." }
  ],
  "unique_constraints": [ { "name": "uq_user_email", "columns": ["user_email"] } ],
  "check_constraints": [ { "name": "ck_user_role", "def": "CHECK ((role)::text = ANY (...))" } ],
  "indexes": [ { "name": "idx_user_email", "unique": false, "method": "btree",
                 "columns": ["user_email"], "where": "deleted_at IS NULL", "def": "CREATE INDEX ..." } ],
  "triggers": [ { "name": "user_set_updated", "def": "CREATE TRIGGER ..." } ],
  "view_definition": null                          // FULL verbatim SQL for views/matviews (never abbreviated), else null
}
```

## Conventions (these are what make parity verifiable)

- **Types** carry length/precision/scale: `varchar(255)`, `numeric(18,2)` — never bare `varchar`/`numeric`.
  Never collapse timezone-bearing types: `timestamp` ≠ `timestamptz`, `datetime` ≠ `datetimeoffset`.
- **`on_delete`/`on_update`** ∈ `NO ACTION | RESTRICT | CASCADE | SET NULL | SET DEFAULT` (verbatim DB action).
- **Indexes** exclude those that merely back a PK/unique constraint (those live under `primary_key` /
  `unique_constraints`). Keep partial-index `where` predicates and expression columns — dropping them makes
  the doc wrong.
- **Enums — `schema.json.enums` holds DB-NATIVE enum types ONLY** (e.g. Postgres `CREATE TYPE … AS ENUM`),
  because the parity check compares against what the database actually defines. The other two kinds are
  represented where the DB actually keeps them, NOT in `enums`:
  - *String + `CHECK (col IN (...))`* (the MSSQL/Prisma pattern — invisible to a type scan; you MUST read
    check constraints): it IS DB-enforced, so put it under that table's `check_constraints`. Surface the
    allowed values in the human docs as "enum-like", but do not invent a DB enum object.
  - *App-level union only* (no DB constraint, e.g. a Zod/TS union): note the allowed values in the column's
    `comment`/description marked "app-enforced (not DB-enforced)". Do NOT add it to `enums` — it is not a
    database object and listing it there is a hallucination against the live schema.
- **Legacy tables** with no ORM entity still exist in the DB → they MUST appear (found via live
  introspection / app queries, never via ORM alone).
- Anything you could not confirm against the live database is flagged, never silently guessed.
