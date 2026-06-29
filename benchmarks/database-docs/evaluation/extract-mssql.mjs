#!/usr/bin/env node
// Deterministic SQL Server ground-truth extractor. Reads sys.* catalogs and emits the same Canonical
// Schema Model (CSM) shape as extract-pg.mjs, so the scorer is dialect-agnostic. Faithful, not normalized.
//
// Usage: DATABASE_URL='sqlserver://localhost:1433;database=Db;user=sa;password=...;encrypt=false;trustServerCertificate=true' \
//        node extract-mssql.mjs [--schemas dbo]

import mssql from 'mssql';

const args = process.argv.slice(2);
const argVal = (f, d) => { const i = args.indexOf(f); return i >= 0 && args[i + 1] ? args[i + 1] : d; };
const schemas = argVal('--schemas', 'dbo').split(',').map((s) => s.trim());
const dsn = process.env.DATABASE_URL;
if (!dsn) { console.error('FATAL: set DATABASE_URL'); process.exit(2); }

// parse sqlserver://host:port;database=..;user=..;password=..;encrypt=..;trustServerCertificate=..
function parseDsn(u) {
  const m = u.match(/^sqlserver:\/\/([^:;]+)(?::(\d+))?(.*)$/i);
  if (!m) throw new Error('bad sqlserver DSN');
  const cfg = { server: m[1], port: m[2] ? Number(m[2]) : 1433, options: { encrypt: false, trustServerCertificate: true, enableArithAbort: true } };
  for (const kv of (m[3] || '').split(';')) {
    const [k, ...r] = kv.split('='); const v = r.join('=');
    if (!k) continue;
    const key = k.trim().toLowerCase();
    if (key === 'database') cfg.database = v;
    else if (key === 'user') cfg.user = v;
    else if (key === 'password') cfg.password = v;
    else if (key === 'encrypt') cfg.options.encrypt = v === 'true';
    else if (key === 'trustservercertificate') cfg.options.trustServerCertificate = v === 'true';
  }
  return cfg;
}

function canonical(v) {
  if (Array.isArray(v)) return v.map(canonical);
  if (v && typeof v === 'object') { const o = {}; for (const k of Object.keys(v).sort()) o[k] = canonical(v[k]); return o; }
  return v;
}
const cmp = (...keys) => (a, b) => { for (const k of keys) { if (a[k] < b[k]) return -1; if (a[k] > b[k]) return 1; } return 0; };
const byNum = (k) => (a, b) => a[k] - b[k];

// Render a faithful SQL Server type string: nvarchar(255), nvarchar(max), decimal(18,2), datetime2(7), etc.
function renderType(t) {
  const n = t.type_name;
  const charTypes = new Set(['varchar', 'char', 'varbinary', 'binary']);
  const ncharTypes = new Set(['nvarchar', 'nchar']);
  if (ncharTypes.has(n)) return `${n}(${t.max_length === -1 ? 'max' : t.max_length / 2})`;
  if (charTypes.has(n)) return `${n}(${t.max_length === -1 ? 'max' : t.max_length})`;
  if (n === 'decimal' || n === 'numeric') return `${n}(${t.precision},${t.scale})`;
  if (['datetime2', 'datetimeoffset', 'time'].includes(n)) return `${n}(${t.scale})`;
  if (n === 'float') return `${n}(${t.precision})`;
  return n;
}
const DELACT = (s) => (s || 'NO_ACTION').replace(/_/g, ' ');

const cfg = parseDsn(dsn);

async function main() {
  const pool = await mssql.connect(cfg);
  const q = async (sql) => (await pool.request().query(sql)).recordset;
  const inList = schemas.map((s) => `'${s.replace(/'/g, "''")}'`).join(',');

  const [{ version }] = await q(`SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(64)) AS version`);

  const rels = await q(`
    SELECT s.name AS [schema], o.name AS [name], CASE o.type WHEN 'U' THEN 'table' WHEN 'V' THEN 'view' END AS kind,
           CAST(ep.value AS nvarchar(max)) AS comment
    FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
    LEFT JOIN sys.extended_properties ep ON ep.major_id=o.object_id AND ep.minor_id=0 AND ep.class=1 AND ep.name='MS_Description'
    WHERE o.type IN ('U','V') AND s.name IN (${inList})`);

  const cols = await q(`
    SELECT s.name AS [schema], o.name AS [table], c.name AS [name], c.column_id AS ordinal,
           ty.name AS type_name, c.max_length, c.precision, c.scale,
           c.is_nullable AS nullable, dc.definition AS [default],
           c.is_identity, c.is_computed, cc.definition AS computed_def, c.collation_name,
           CAST(ep.value AS nvarchar(max)) AS comment
    FROM sys.columns c
    JOIN sys.objects o ON o.object_id=c.object_id
    JOIN sys.schemas s ON s.schema_id=o.schema_id
    JOIN sys.types ty ON ty.user_type_id=c.user_type_id
    LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id
    LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id
    LEFT JOIN sys.extended_properties ep ON ep.major_id=c.object_id AND ep.minor_id=c.column_id AND ep.class=1 AND ep.name='MS_Description'
    WHERE o.type IN ('U','V') AND s.name IN (${inList})
    ORDER BY 1,2,4`);

  const keyCons = await q(`
    SELECT s.name AS [schema], t.name AS [table], kc.name AS [name], kc.type AS contype,
           c.name AS col, ic.key_ordinal AS ord
    FROM sys.key_constraints kc
    JOIN sys.tables t ON t.object_id=kc.parent_object_id
    JOIN sys.schemas s ON s.schema_id=t.schema_id
    JOIN sys.index_columns ic ON ic.object_id=kc.parent_object_id AND ic.index_id=kc.unique_index_id
    JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
    WHERE s.name IN (${inList})
    ORDER BY 1,2,3,6`);

  const fkRows = await q(`
    SELECT s.name AS [schema], t.name AS [table], fk.name AS [name],
           rs.name AS ref_schema, rt.name AS ref_table,
           fk.delete_referential_action_desc AS on_delete, fk.update_referential_action_desc AS on_update,
           c.name AS col, rc.name AS ref_col, fkc.constraint_column_id AS ord
    FROM sys.foreign_keys fk
    JOIN sys.tables t ON t.object_id=fk.parent_object_id
    JOIN sys.schemas s ON s.schema_id=t.schema_id
    JOIN sys.tables rt ON rt.object_id=fk.referenced_object_id
    JOIN sys.schemas rs ON rs.schema_id=rt.schema_id
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
    JOIN sys.columns c ON c.object_id=fkc.parent_object_id AND c.column_id=fkc.parent_column_id
    JOIN sys.columns rc ON rc.object_id=fkc.referenced_object_id AND rc.column_id=fkc.referenced_column_id
    WHERE s.name IN (${inList})
    ORDER BY 1,2,3,10`);

  const checks = await q(`
    SELECT s.name AS [schema], t.name AS [table], cc.name AS [name], cc.definition AS def
    FROM sys.check_constraints cc
    JOIN sys.tables t ON t.object_id=cc.parent_object_id
    JOIN sys.schemas s ON s.schema_id=t.schema_id
    WHERE s.name IN (${inList}) ORDER BY 1,2,3`);

  const idxRows = await q(`
    SELECT s.name AS [schema], t.name AS [table], i.name AS [name], i.is_unique AS [unique],
           i.type_desc AS method, i.has_filter, i.filter_definition AS where_pred,
           c.name AS col, ic.key_ordinal AS ord
    FROM sys.indexes i
    JOIN sys.tables t ON t.object_id=i.object_id
    JOIN sys.schemas s ON s.schema_id=t.schema_id
    JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id AND ic.is_included_column=0
    JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
    WHERE s.name IN (${inList}) AND i.is_primary_key=0 AND i.is_unique_constraint=0 AND i.type>0 AND i.name IS NOT NULL
    ORDER BY 1,2,3,9`);

  const trigs = await q(`
    SELECT s.name AS [schema], t.name AS [table], tr.name AS [name], OBJECT_DEFINITION(tr.object_id) AS def
    FROM sys.triggers tr JOIN sys.tables t ON t.object_id=tr.parent_id
    JOIN sys.schemas s ON s.schema_id=t.schema_id
    WHERE s.name IN (${inList}) AND tr.is_ms_shipped=0 ORDER BY 1,2,3`);

  const routines = await q(`
    SELECT s.name AS [schema], o.name AS [name],
           CASE o.type WHEN 'P' THEN 'procedure' ELSE 'function' END AS kind
    FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
    WHERE o.type IN ('P','FN','IF','TF') AND o.is_ms_shipped=0 AND s.name IN (${inList})
    ORDER BY 1,2`);

  const seqs = await q(`
    SELECT s.name AS [schema], sq.name AS [name]
    FROM sys.sequences sq JOIN sys.schemas s ON s.schema_id=sq.schema_id
    WHERE s.name IN (${inList}) ORDER BY 1,2`);

  const viewdefs = await q(`
    SELECT s.name AS [schema], v.name AS [name], m.definition
    FROM sys.views v JOIN sys.schemas s ON s.schema_id=v.schema_id
    JOIN sys.sql_modules m ON m.object_id=v.object_id
    WHERE s.name IN (${inList}) ORDER BY 1,2`);

  // assemble
  const key = (s, t) => `${s}.${t}`;
  const tableMap = new Map();
  for (const r of rels) tableMap.set(key(r.schema, r.name), {
    schema: r.schema, name: r.name, kind: r.kind, comment: r.comment, row_estimate: null,
    columns: [], primary_key: null, foreign_keys: [], unique_constraints: [],
    check_constraints: [], indexes: [], triggers: [], view_definition: null,
  });
  for (const c of cols) {
    const t = tableMap.get(key(c.schema, c.table)); if (!t) continue;
    t.columns.push({ name: c.name, ordinal: c.ordinal, type: renderType(c), nullable: !!c.nullable,
      default: c.is_computed ? c.computed_def : c.default, is_identity: !!c.is_identity, is_generated: !!c.is_computed,
      collation: c.collation_name, comment: c.comment, enum_ref: null, domain_ref: null });
  }
  const groupCols = (rows, kf) => { const m = new Map(); for (const r of rows) { const k = kf(r); (m.get(k) || m.set(k, []).get(k)).push(r); } return m; };
  // PK/unique
  const kcMap = groupCols(keyCons, (r) => `${r.schema}.${r.table}.${r.name}`);
  for (const [, rows] of kcMap) {
    const r0 = rows[0]; const t = tableMap.get(key(r0.schema, r0.table)); if (!t) continue;
    const columns = rows.slice().sort(byNum('ord')).map((x) => x.col);
    if (r0.contype === 'PK') t.primary_key = { name: r0.name, columns };
    else t.unique_constraints.push({ name: r0.name, columns });
  }
  // FKs
  const fkMap = groupCols(fkRows, (r) => `${r.schema}.${r.table}.${r.name}`);
  for (const [, rows] of fkMap) {
    const r0 = rows[0]; const t = tableMap.get(key(r0.schema, r0.table)); if (!t) continue;
    const ordered = rows.slice().sort(byNum('ord'));
    t.foreign_keys.push({ name: r0.name, columns: ordered.map((x) => x.col), ref_schema: r0.ref_schema,
      ref_table: r0.ref_table, ref_columns: ordered.map((x) => x.ref_col),
      on_delete: DELACT(r0.on_delete), on_update: DELACT(r0.on_update), def: null });
  }
  for (const c of checks) { const t = tableMap.get(key(c.schema, c.table)); if (t) t.check_constraints.push({ name: c.name, def: c.def }); }
  const idxMap = groupCols(idxRows, (r) => `${r.schema}.${r.table}.${r.name}`);
  for (const [, rows] of idxMap) {
    const r0 = rows[0]; const t = tableMap.get(key(r0.schema, r0.table)); if (!t) continue;
    const columns = rows.slice().sort(byNum('ord')).map((x) => x.col);
    t.indexes.push({ name: r0.name, unique: !!r0.unique, method: r0.method, columns,
      where: r0.has_filter ? r0.where_pred : null, def: null });
  }
  for (const tg of trigs) { const t = tableMap.get(key(tg.schema, tg.table)); if (t) t.triggers.push({ name: tg.name, def: tg.def }); }
  for (const v of viewdefs) { const t = tableMap.get(key(v.schema, v.name)); if (t) t.view_definition = v.definition; }

  for (const t of tableMap.values()) {
    t.columns.sort(byNum('ordinal'));
    t.foreign_keys.sort(cmp('name')); t.unique_constraints.sort(cmp('name'));
    t.check_constraints.sort(cmp('name')); t.indexes.sort(cmp('name')); t.triggers.sort(cmp('name'));
  }
  const tables = [...tableMap.values()].sort(cmp('schema', 'name'));
  const out = {
    meta: { engine: 'mssql', engine_version: version, schemas: schemas.slice().sort(), extractor: 'extract-mssql.mjs', csm_version: 1 },
    tables,
    enums: [], // SQL Server has no native enums (modeled as CHECK constraints, captured above)
    domains: [],
    sequences: seqs.map((s) => ({ schema: s.schema, name: s.name })).sort(cmp('schema', 'name')),
    routines: routines.map((r) => ({ schema: r.schema, name: r.name, kind: r.kind, returns: null, arguments: null, language: 'tsql' })).sort(cmp('schema', 'name')),
  };
  process.stdout.write(JSON.stringify(canonical(out), null, 2) + '\n');
  await pool.close();
}
main().catch((e) => { console.error('FATAL:', e.message); process.exit(1); });
