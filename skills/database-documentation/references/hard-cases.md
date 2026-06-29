# Hard cases — where naive documentation goes wrong

Each of these silently produces incorrect docs if handled naively. Handle them explicitly.

- **Enums, three kinds — keep them distinct.**
  - *Native* (Postgres `CREATE TYPE … AS ENUM`): read `pg_enum`; list values + the columns using them.
  - *String + CHECK* (SQL Server, Prisma-on-MSSQL, some Postgres): the column is `varchar`/`nvarchar` with a
    `CHECK (col IN (...))`. Invisible to a type scan — **read check constraints** or you document it as a
    plain string and lose every allowed value.
  - *App-level only* (Zod/TS union, no DB constraint): note the values in the column description marked
    "app-enforced (not DB-enforced)". Do NOT put it in `schema.json.enums` — only DB-native enum types go
    there; a string+CHECK set goes under `check_constraints`. Claiming a DB enum that the DB doesn't define
    is a hallucination against the live schema (it will fail the parity check).
- **JSON / JSONB columns** — type is `jsonb`/`nvarchar(max)`, but the *shape* matters. Infer it from the ORM
  field type / Zod schema, sampled rows (read-only `LIMIT`), or seeds; document the shape as a fenced
  sub-block labeled "inferred". On MSSQL, detect JSON-in-`NVarChar(Max)`.
- **Polymorphic relations** — `*_type` + `*_id` pairs with no real FK. Detect by naming + ORM markers + app
  queries; draw a dashed/labeled logical edge and note "no enforced FK".
- **Soft deletes** — `deleted_at` / `is_deleted` / `is_active` (+ ORM `@DeleteDateColumn`). Note on the table
  and in any partial index `WHERE deleted_at IS NULL`.
- **Composite keys** — multi-column PK/unique/FK; document the full tuple, mark each participating column.
- **Partial / expression indexes** — keep the `WHERE` predicate and the expression (`lower(email)`); dropping
  them makes the doc wrong.
- **Views, materialized views, generated/computed columns** — separate `kind`; capture the defining query;
  label generated columns with their expression.
- **Multi-schema databases** — partition by schema first, then domain; note cross-schema FKs.
- **Case-sensitivity / collation** — preserve `citext` / `COLLATE`; it changes uniqueness semantics, so
  surface it in the type/description.
- **Legacy tables with no ORM entity** — exist only in the live DB / app queries, never via ORM extraction.
  They MUST appear, and belong in `issues/` as DB-only / maintenance-risk.
- **Framework/tooling tables are not domain schema** — migration/metadata tables created by the ORM or
  migration tool (`typeorm_metadata`, `migrations`, `knex_migrations`/`knex_migrations_lock`,
  `__EFMigrationsHistory`, `django_migrations`, `schema_migrations`, `_prisma_migrations`, NextAuth's
  `VerificationToken`/`Account`/`Session`). Document them (they exist), but mark them "framework/tooling
  (not application schema)" so they don't pollute domain ERDs or get mislabeled as business "legacy" tables.
- **Identity vs serial vs sequence defaults** — `serial`, `GENERATED … AS IDENTITY`, and `int + nextval(...)`
  are the same intent rendered differently; document the real default the catalog reports; do not flag the
  rendering difference as drift.
- **timestamp vs timestamptz / datetime vs datetimeoffset** — never collapse; timezone is semantic.
- **Extension-provided objects are NOT your schema** — `citext`, `pg_trgm`, `uuid-ossp`, `postgis` etc. install
  dozens of functions/operators (often into `public`). Documenting them is noise that buries the app's real
  surface. Exclude objects owned by an extension (`pg_depend.deptype='e'`); document only what your app owns.
  (Keep the extension column *types* you use, e.g. a `citext` column — that's your schema; just drop the
  extension's internal functions.)
