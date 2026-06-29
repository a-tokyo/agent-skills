# Live introspection — exact catalog queries per engine

## Contents
- How to run them
- PostgreSQL
- Count-gate queries (population-matched — Phase 4)
- MySQL / MariaDB
- SQL Server (`sys.*`) — runnable
- SQLite — runnable
- The check-constraint enum trap

## How to run them

Run these yourself against the live database (via the DB MCP, `docker compose exec … psql`, or a local
CLI). Transcribe results faithfully into the CSM (`references/csm-contract.md`). `information_schema` is
portable but lossy; the engine-native catalogs (`pg_catalog`, `sys.*`, `SHOW CREATE`, `PRAGMA`) carry the
things that actually matter for correctness — `ON DELETE`, partial-index predicates, check constraints,
identity/generated, collation. Prefer the native catalog; cross-check with `information_schema`.

## PostgreSQL (`pg_catalog`)

Restrict to your target schema(s) (usually `public`). Key queries:

**Relations** (tables/views/matviews/partitioned/foreign) with comments + approx row counts:
```sql
SELECT n.nspname AS schema, c.relname AS name, c.relkind, obj_description(c.oid) AS comment,
       CASE WHEN c.relkind IN ('r','p','m') THEN c.reltuples::bigint END AS row_estimate
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname=ANY($1) AND c.relkind IN ('r','p','v','m','f') AND NOT c.relispartition ORDER BY 1,2;
-- relkind: r=table p=partitioned v=view m=matview f=foreign
-- `AND NOT c.relispartition` documents a partitioned table as ONE entity (the parent); skip child partitions.
```
> `reltuples` is an ESTIMATE and is **`-1` when the table was never ANALYZEd** (common on fresh/dev DBs). Do
> not print `-1` (or treat it as 0) as a row count — label it "estimate (unanalyzed)", omit it, or run an
> explicit `COUNT(*)` if an accurate figure matters. Row counts are volatile data, not schema; keep them out
> of the parity diff.

**Columns** (full type, nullability, default, identity, generated, collation, comment, enum/domain ref):
```sql
SELECT n.nspname schema, c.relname "table", a.attname name, a.attnum ordinal,
       format_type(a.atttypid,a.atttypmod) AS type,        -- includes length/precision
       NOT a.attnotnull AS nullable,
       pg_get_expr(ad.adbin,ad.adrelid) AS default,
       a.attidentity<>'' AS is_identity, a.attgenerated<>'' AS is_generated,
       (SELECT collname FROM pg_collation cl WHERE cl.oid=a.attcollation AND cl.collname<>'default') AS collation,
       col_description(c.oid,a.attnum) AS comment, t.typtype, t.typname AS udt
FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_type t ON t.oid=a.atttypid LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
WHERE n.nspname=ANY($1) AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped
ORDER BY 1,2,4;
```

**Constraints** — PK/unique/FK/check, with ordered columns and FK actions. `pg_get_constraintdef(oid)`
gives the full text (including `ON DELETE`), and `confdeltype`/`confupdtype` give the action codes
(`a`=NO ACTION `r`=RESTRICT `c`=CASCADE `n`=SET NULL `d`=SET DEFAULT):
```sql
SELECT n.nspname schema, rel.relname "table", con.conname name, con.contype,  -- p/u/f/c
       pg_get_constraintdef(con.oid) AS def, con.confdeltype, con.confupdtype,
       (SELECT frel.relname FROM pg_class frel WHERE frel.oid=con.confrelid) AS ref_table
FROM pg_constraint con JOIN pg_class rel ON rel.oid=con.conrelid JOIN pg_namespace n ON n.oid=rel.relnamespace
WHERE n.nspname=ANY($1) AND con.contype IN ('p','u','f','c') ORDER BY 1,2,3;
```
For exact column ordering use `unnest(con.conkey) WITH ORDINALITY` joined to `pg_attribute`.

**Indexes** (exclude those backing a constraint; keep partial predicate + method):
```sql
SELECT n.nspname schema, t.relname "table", i.relname name, ix.indisunique AS unique, am.amname AS method,
       pg_get_indexdef(ix.indexrelid) AS def, pg_get_expr(ix.indpred,ix.indrelid) AS where_pred,
       EXISTS(SELECT 1 FROM pg_constraint c WHERE c.conindid=ix.indexrelid) AS backs_constraint
FROM pg_index ix JOIN pg_class i ON i.oid=ix.indexrelid JOIN pg_class t ON t.oid=ix.indrelid
JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_am am ON am.oid=i.relam
WHERE n.nspname=ANY($1) ORDER BY 1,2,3;
```

**Enums**: `pg_enum`+`pg_type` (`array_agg(enumlabel ORDER BY enumsortorder)`).
**Domains**: `pg_type WHERE typtype='d'` + `format_type(typbasetype,typtypmod)`.
**Sequences**: `pg_sequences`. **Triggers**: `pg_trigger WHERE NOT tgisinternal` + `pg_get_triggerdef`.
**Routines**: `pg_proc` joined to your schema + `pg_get_function_result`/`pg_get_function_arguments`.
  **Exclude extension-provided functions** (citext, pg_trgm, uuid-ossp, postgis, …) — they are not your
  application's schema and documenting them is noise. Filter them out:
  `AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e')`. The same
  `deptype='e'` test excludes any extension-owned table/type/sequence — only document objects your app owns.
**View bodies**: `pg_get_viewdef(oid, true)`.

## Count-gate queries (population-matched — Phase 4)

The count gate ONLY works if each count selects the SAME population the extraction/CSM defines. The two that
trip every real DB: exclude constraint-backing indexes, and exclude extension-owned objects. Postgres:

```sql
-- tables (base only)         -> matches CSM tables of kind table/partitioned/foreign
-- NOT c.relispartition: document a partitioned table as ONE logical entity (the 'p' parent); child
-- partitions (relkind 'r', relispartition=true) would otherwise inflate the count and false-fail the gate.
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1) AND c.relkind IN ('r','p','f') AND NOT c.relispartition;
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1) AND c.relkind IN ('v','m');  -- views
-- columns
SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1) AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped;
-- FKs / PKs / uniques / CHECKs  (NEVER information_schema.check_constraints: it adds a row per NOT NULL!)
SELECT contype, count(*) FROM pg_constraint con JOIN pg_class r ON r.oid=con.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=ANY($1) AND contype IN ('p','u','f','c') GROUP BY contype;
-- indexes EXCLUDING constraint-backing ones (else 392 vs the 216 you document)
SELECT count(*) FROM pg_index ix JOIN pg_class t ON t.oid=ix.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=ANY($1) AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid=ix.indexrelid);
-- enums / sequences / triggers
SELECT count(DISTINCT t.oid) FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=ANY($1);
SELECT count(*) FROM pg_sequences WHERE schemaname=ANY($1);
SELECT count(*) FROM pg_trigger tg JOIN pg_class c ON c.oid=tg.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1) AND NOT tg.tgisinternal;
-- routines EXCLUDING extension-owned (else 96 vs the 8 app functions you document)
SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1) AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e');
```
Unsafe-for-counting views to avoid: `information_schema.check_constraints` (includes synthetic `IS NOT NULL`
rows — can be 100×+ the real count), and any `reltuples`-based row figure (`-1` = unanalyzed).

## MySQL / MariaDB

Schema = the database name; there is no separate `public`. Runnable:
```sql
-- tables / columns
SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE();
SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, COLUMN_COMMENT,
       GENERATION_EXPRESSION                            -- generated columns
  FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION;
-- COLUMN_TYPE carries enum values inline: enum('a','b','c'); ON UPDATE lives in EXTRA.
-- FKs WITH actions
SELECT rc.CONSTRAINT_NAME, rc.TABLE_NAME, kcu.COLUMN_NAME, kcu.REFERENCED_TABLE_NAME, kcu.REFERENCED_COLUMN_NAME,
       rc.DELETE_RULE, rc.UPDATE_RULE
  FROM information_schema.REFERENTIAL_CONSTRAINTS rc
  JOIN information_schema.KEY_COLUMN_USAGE kcu ON kcu.CONSTRAINT_NAME=rc.CONSTRAINT_NAME AND kcu.CONSTRAINT_SCHEMA=rc.CONSTRAINT_SCHEMA
  WHERE rc.CONSTRAINT_SCHEMA=DATABASE();
-- PK/unique/index: information_schema.STATISTICS (NON_UNIQUE, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, INDEX_TYPE)
-- check constraints: information_schema.CHECK_CONSTRAINTS (MySQL 8.0.16+/MariaDB 10.2+)
-- routines/triggers/views: information_schema.{ROUTINES,TRIGGERS,VIEWS}
```
`SHOW CREATE TABLE t` is the authoritative fallback for index types, `ON UPDATE`, and generated columns.

## SQL Server (`sys.*`) — runnable

```sql
-- tables / views (+ MS_Description comment)
SELECT s.name [schema], o.name, o.type FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE o.type IN ('U','V');
-- columns (type+len/prec, nullable, default, identity, computed, collation)
SELECT s.name, o.name [table], c.name, c.column_id, ty.name type_name, c.max_length, c.precision, c.scale,
       c.is_nullable, dc.definition [default], c.is_identity, c.is_computed, cc.definition computed_def, c.collation_name
  FROM sys.columns c JOIN sys.objects o ON o.object_id=c.object_id JOIN sys.schemas s ON s.schema_id=o.schema_id
  JOIN sys.types ty ON ty.user_type_id=c.user_type_id
  LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id
  LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id
  WHERE o.type IN ('U','V');
-- FKs WITH delete/update actions
SELECT fk.name, OBJECT_NAME(fk.parent_object_id) [table], OBJECT_NAME(fk.referenced_object_id) ref_table,
       fk.delete_referential_action_desc, fk.update_referential_action_desc FROM sys.foreign_keys fk;
-- PK/unique: sys.key_constraints + sys.index_columns ; CHECKs: sys.check_constraints (definition)
-- indexes EXCLUDING constraint-backing: sys.indexes WHERE is_primary_key=0 AND is_unique_constraint=0 AND type>0 (+ has_filter/filter_definition)
-- sequences: sys.sequences ; triggers: sys.triggers WHERE is_ms_shipped=0 ; routines: sys.objects type IN ('P','FN','IF','TF') AND is_ms_shipped=0
```
**No native enum** — modeled as `nvarchar` + `CHECK (col IN (...))` (→ `check_constraints`) or app-level (→ description). Render `nvarchar(n)` from `max_length` (`/2`; `-1`=`max`), `decimal(p,s)`, `datetime2(scale)`; never collapse `datetimeoffset`.

## SQLite — runnable
```sql
SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table','view','index','trigger') AND name NOT LIKE 'sqlite_%';
PRAGMA table_info('t');         -- cols: name, type, notnull, dflt_value, pk
PRAGMA foreign_key_list('t');   -- id, seq, table, from, to, on_update, on_delete
PRAGMA index_list('t');         -- name, unique, origin (c=CREATE INDEX, pk, u) -> origin='c' are the non-constraint indexes
PRAGMA index_info('idx');       -- columns
```
SQLite has no native enum (CHECK pattern) and no sequences/stored routines; `INTEGER PRIMARY KEY` is the rowid alias.

## The check-constraint enum trap

On SQL Server (and the Prisma-on-MSSQL pattern, and some Postgres schemas) an "enum" is a plain string
column plus a `CHECK (col IN ('a','b','c'))`. A column-type scan shows only `nvarchar` and misses the
allowed values entirely. You MUST read check constraints and surface these as enums in the CSM, or the
documentation silently understates the schema.
