#!/usr/bin/env node
// Deterministic PostgreSQL ground-truth extractor for the database-docs skill build.
// Reads the live catalog (pg_catalog) and emits a Canonical Schema Model (CSM) as sorted JSON.
// This is the ORACLE: code reading the catalog cannot hallucinate or omit. Faithful, not normalized
// (type canonicalization happens in the scorer, so the oracle stays a pure mirror of the DB).
//
// Usage: DATABASE_URL=postgres://bench:bench@localhost:5440/bench node extract-pg.mjs [--schemas public]
// Output: canonical IR JSON on stdout (keys sorted, arrays sorted -> byte-identical across runs).

import pg from 'pg';

const args = process.argv.slice(2);
function argVal(flag, def) {
  const i = args.indexOf(flag);
  return i >= 0 && args[i + 1] ? args[i + 1] : def;
}
const schemas = argVal('--schemas', 'public').split(',').map((s) => s.trim());
const dsn = process.env.DATABASE_URL;
if (!dsn) {
  console.error('FATAL: set DATABASE_URL');
  process.exit(2);
}

// ---- deterministic JSON: recursively sort object keys; arrays are pre-sorted by callers ----
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value).sort()) out[k] = canonical(value[k]);
    return out;
  }
  return value;
}
const byKey = (k) => (a, b) => (a[k] < b[k] ? -1 : a[k] > b[k] ? 1 : 0);
const cmp = (...keys) => (a, b) => {
  for (const k of keys) { if (a[k] < b[k]) return -1; if (a[k] > b[k]) return 1; }
  return 0;
};
const DELACT = { a: 'NO ACTION', r: 'RESTRICT', c: 'CASCADE', n: 'SET NULL', d: 'SET DEFAULT' };
const RELKIND = { r: 'table', p: 'partitioned', v: 'view', m: 'materialized_view', f: 'foreign' };

const client = new pg.Client({ connectionString: dsn });

async function q(sql, params = []) {
  const r = await client.query(sql, params);
  return r.rows;
}

async function main() {
  await client.connect();
  const [{ version }] = await q('SELECT current_setting($1) AS version', ['server_version']);

  // ---- relations (tables/views/matviews/partitioned/foreign) ----
  const rels = await q(
    `SELECT n.nspname AS schema, c.relname AS name, c.relkind AS relkind,
            obj_description(c.oid) AS comment,
            CASE WHEN c.relkind IN ('r','p','m') THEN c.reltuples::bigint ELSE NULL END AS row_estimate
       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname = ANY($1) AND c.relkind IN ('r','p','v','m','f')
      ORDER BY 1,2`, [schemas]);

  // ---- columns ----
  const cols = await q(
    `SELECT n.nspname AS schema, c.relname AS "table", a.attname AS name, a.attnum AS ordinal,
            format_type(a.atttypid, a.atttypmod) AS type,
            (NOT a.attnotnull) AS nullable,
            pg_get_expr(ad.adbin, ad.adrelid) AS "default",
            (a.attidentity <> '') AS is_identity,
            (a.attgenerated <> '') AS is_generated,
            col_description(c.oid, a.attnum) AS comment,
            (SELECT collname FROM pg_collation cl WHERE cl.oid=a.attcollation AND cl.collname<>'default') AS collation,
            t.typtype AS typtype, t.typname AS type_name,
            et.typtype AS elem_typtype, et.typname AS elem_type_name  -- element type for array columns
       FROM pg_attribute a
       JOIN pg_class c ON c.oid=a.attrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_type t ON t.oid=a.atttypid
       LEFT JOIN pg_type et ON et.oid=t.typelem AND t.typtype='b' AND t.typelem<>0
       LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
      WHERE n.nspname = ANY($1) AND c.relkind IN ('r','p','v','m','f')
            AND a.attnum > 0 AND NOT a.attisdropped
      ORDER BY 1,2,4`, [schemas]);

  // ---- constraints (pk/unique/fk/check) with ordered columns + referenced cols ----
  const cons = await q(
    `SELECT n.nspname AS schema, rel.relname AS "table", con.conname AS name, con.contype AS contype,
            pg_get_constraintdef(con.oid) AS def,
            con.confdeltype AS confdeltype, con.confupdtype AS confupdtype,
            (SELECT frel.relname FROM pg_class frel WHERE frel.oid=con.confrelid) AS ref_table,
            (SELECT nf.nspname FROM pg_class frel JOIN pg_namespace nf ON nf.oid=frel.relnamespace WHERE frel.oid=con.confrelid) AS ref_schema,
            (SELECT array_agg(att.attname ORDER BY u.ord)::text[]
               FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
               JOIN pg_attribute att ON att.attrelid=con.conrelid AND att.attnum=u.attnum) AS columns,
            (SELECT array_agg(att.attname ORDER BY u.ord)::text[]
               FROM unnest(con.confkey) WITH ORDINALITY AS u(attnum, ord)
               JOIN pg_attribute att ON att.attrelid=con.confrelid AND att.attnum=u.attnum) AS ref_columns
       FROM pg_constraint con
       JOIN pg_class rel ON rel.oid=con.conrelid
       JOIN pg_namespace n ON n.oid=rel.relnamespace
      WHERE n.nspname = ANY($1) AND con.contype IN ('p','u','f','c')
      ORDER BY 1,2,3`, [schemas]);

  // ---- indexes (skip those backing pk/unique constraints; capture partial/expression/method) ----
  const idxs = await q(
    `SELECT n.nspname AS schema, t.relname AS "table", i.relname AS name,
            ix.indisunique AS "unique", ix.indisprimary AS is_primary,
            am.amname AS method,
            pg_get_indexdef(ix.indexrelid) AS def,
            pg_get_expr(ix.indpred, ix.indrelid) AS where_pred,
            (SELECT array_agg(CASE WHEN k.attnum=0 THEN '(expr)' ELSE att.attname END ORDER BY k.ord)::text[]
               FROM unnest(ix.indkey::int[]) WITH ORDINALITY AS k(attnum, ord)
               LEFT JOIN pg_attribute att ON att.attrelid=ix.indrelid AND att.attnum=k.attnum) AS columns,
            EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid=ix.indexrelid) AS backs_constraint
       FROM pg_index ix
       JOIN pg_class i ON i.oid=ix.indexrelid
       JOIN pg_class t ON t.oid=ix.indrelid
       JOIN pg_namespace n ON n.oid=t.relnamespace
       JOIN pg_am am ON am.oid=i.relam
      WHERE n.nspname = ANY($1)
      ORDER BY 1,2,3`, [schemas]);

  // ---- enums ----
  const enums = await q(
    `SELECT n.nspname AS schema, t.typname AS name,
            array_agg(e.enumlabel ORDER BY e.enumsortorder)::text[] AS values
       FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid
       JOIN pg_namespace n ON n.oid=t.typnamespace
      WHERE n.nspname = ANY($1)
        AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=t.oid AND d.deptype='e')  -- exclude extension-provided
      GROUP BY 1,2 ORDER BY 1,2`, [schemas]);

  // ---- domains ----
  const domains = await q(
    `SELECT n.nspname AS schema, t.typname AS name, format_type(t.typbasetype, t.typtypmod) AS base_type,
            (SELECT string_agg(pg_get_constraintdef(c.oid), '; ' ORDER BY c.conname)
               FROM pg_constraint c WHERE c.contypid=t.oid) AS constraints
       FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
      WHERE n.nspname = ANY($1) AND t.typtype='d'
        AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=t.oid AND d.deptype='e')  -- exclude extension-provided
      ORDER BY 1,2`, [schemas]);

  // ---- sequences ----
  const seqs = await q(
    `SELECT schemaname AS schema, sequencename AS name
       FROM pg_sequences WHERE schemaname = ANY($1) ORDER BY 1,2`, [schemas]);

  // ---- triggers (user-defined only) ----
  const trigs = await q(
    `SELECT n.nspname AS schema, c.relname AS "table", tg.tgname AS name,
            pg_get_triggerdef(tg.oid) AS def
       FROM pg_trigger tg
       JOIN pg_class c ON c.oid=tg.tgrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname = ANY($1) AND NOT tg.tgisinternal
      ORDER BY 1,2,3`, [schemas]);

  // ---- routines (functions/procedures defined in target schemas) ----
  const routines = await q(
    `SELECT n.nspname AS schema, p.proname AS name,
            CASE p.prokind WHEN 'f' THEN 'function' WHEN 'p' THEN 'procedure' WHEN 'a' THEN 'aggregate' WHEN 'w' THEN 'window' END AS kind,
            pg_get_function_result(p.oid) AS returns,
            pg_get_function_arguments(p.oid) AS arguments,
            l.lanname AS language
       FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       JOIN pg_language l ON l.oid=p.prolang
      WHERE n.nspname = ANY($1)
        AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e')  -- exclude extension-provided
      ORDER BY 1,2,5`, [schemas]);

  // ---- view definitions ----
  const viewdefs = await q(
    `SELECT n.nspname AS schema, c.relname AS name, pg_get_viewdef(c.oid, true) AS definition
       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname = ANY($1) AND c.relkind IN ('v','m')
      ORDER BY 1,2`, [schemas]);

  // ---- assemble per-table CSM ----
  const key = (s, t) => `${s}.${t}`;
  const tableMap = new Map();
  for (const r of rels) {
    tableMap.set(key(r.schema, r.name), {
      schema: r.schema, name: r.name, kind: RELKIND[r.relkind] || r.relkind,
      comment: r.comment, row_estimate: r.row_estimate == null ? null : Number(r.row_estimate),
      columns: [], primary_key: null, foreign_keys: [], unique_constraints: [],
      check_constraints: [], indexes: [], triggers: [], view_definition: null,
    });
  }
  for (const c of cols) {
    const t = tableMap.get(key(c.schema, c.table)); if (!t) continue;
    t.columns.push({
      name: c.name, ordinal: c.ordinal, type: c.type, nullable: c.nullable,
      default: c.default, is_identity: c.is_identity, is_generated: c.is_generated,
      collation: c.collation, comment: c.comment,
      enum_ref: c.typtype === 'e' ? c.type_name : (c.elem_typtype === 'e' ? c.elem_type_name : null),
      domain_ref: c.typtype === 'd' ? c.type_name : (c.elem_typtype === 'd' ? c.elem_type_name : null),
    });
  }
  for (const c of cons) {
    const t = tableMap.get(key(c.schema, c.table)); if (!t) continue;
    if (c.contype === 'p') t.primary_key = { name: c.name, columns: c.columns || [] };
    else if (c.contype === 'u') t.unique_constraints.push({ name: c.name, columns: c.columns || [] });
    else if (c.contype === 'c') t.check_constraints.push({ name: c.name, def: c.def });
    else if (c.contype === 'f') t.foreign_keys.push({
      name: c.name, columns: c.columns || [], ref_schema: c.ref_schema, ref_table: c.ref_table,
      ref_columns: c.ref_columns || [], on_delete: DELACT[c.confdeltype], on_update: DELACT[c.confupdtype], def: c.def,
    });
  }
  for (const ix of idxs) {
    const t = tableMap.get(key(ix.schema, ix.table)); if (!t) continue;
    if (ix.backs_constraint) continue; // pk/unique already captured as constraints
    t.indexes.push({ name: ix.name, unique: ix.unique, method: ix.method, columns: ix.columns || [], where: ix.where_pred, def: ix.def });
  }
  for (const tg of trigs) {
    const t = tableMap.get(key(tg.schema, tg.table)); if (!t) continue;
    t.triggers.push({ name: tg.name, def: tg.def });
  }
  for (const v of viewdefs) {
    const t = tableMap.get(key(v.schema, v.name)); if (t) t.view_definition = v.definition;
  }
  // sort inner arrays deterministically
  for (const t of tableMap.values()) {
    t.columns.sort(byKey('ordinal'));
    t.foreign_keys.sort(cmp('name'));
    t.unique_constraints.sort(cmp('name'));
    t.check_constraints.sort(cmp('name'));
    t.indexes.sort(cmp('name'));
    t.triggers.sort(cmp('name'));
    if (t.primary_key) t.primary_key.columns = t.primary_key.columns || [];
  }

  const tables = [...tableMap.values()].sort(cmp('schema', 'name'));
  const out = {
    meta: { engine: 'postgres', engine_version: version, schemas: schemas.slice().sort(),
            extractor: 'extract-pg.mjs', csm_version: 1 },
    tables,
    enums: enums.map((e) => ({ schema: e.schema, name: e.name, values: e.values })).sort(cmp('schema', 'name')),
    domains: domains.map((d) => ({ schema: d.schema, name: d.name, base_type: d.base_type, constraints: d.constraints })).sort(cmp('schema', 'name')),
    sequences: seqs.map((s) => ({ schema: s.schema, name: s.name })).sort(cmp('schema', 'name')),
    routines: routines.map((r) => ({ schema: r.schema, name: r.name, kind: r.kind, returns: r.returns, arguments: r.arguments, language: r.language })).sort(cmp('schema', 'name', 'arguments')),
  };
  process.stdout.write(JSON.stringify(canonical(out), null, 2) + '\n');
  await client.end();
}

main().catch((e) => { console.error('FATAL:', e.message); process.exit(1); });
