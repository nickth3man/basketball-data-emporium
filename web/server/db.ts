import path from "node:path";
import { fileURLToPath } from "node:url";
import { DuckDBInstance, type DuckDBConnection, type DuckDBValue } from "@duckdb/node-api";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DB_PATH = process.env.DUCKDB_PATH ?? path.resolve(__dirname, "../../data/nba.duckdb");

export interface TableInfo {
  schema: string;
  name: string;
  estimatedRows: number;
}

let connectionPromise: Promise<DuckDBConnection> | null = null;
let tableCache: TableInfo[] | null = null;

function getConnection(): Promise<DuckDBConnection> {
  connectionPromise ??= DuckDBInstance.create(DB_PATH, { access_mode: "READ_ONLY" }).then(
    (instance) => instance.connect(),
  );
  return connectionPromise;
}

export async function listTables(): Promise<TableInfo[]> {
  if (tableCache) return tableCache;
  const conn = await getConnection();
  const reader = await conn.runAndReadAll(
    `SELECT schema_name, table_name, estimated_size
     FROM duckdb_tables()
     ORDER BY schema_name, table_name`,
  );
  const rows = reader.getRowObjectsJson() as {
    schema_name: string;
    table_name: string;
    estimated_size: number | bigint;
  }[];
  tableCache = rows.map((r) => ({
    schema: r.schema_name,
    name: r.table_name,
    estimatedRows: Number(r.estimated_size),
  }));
  return tableCache;
}

/** Resolves a requested table name against the real catalog, preventing
 *  identifier injection when the name is interpolated into SQL below. */
export async function resolveTable(name: string): Promise<TableInfo | null> {
  const tables = await listTables();
  return tables.find((t) => t.name === name) ?? null;
}

function quoteIdent(ident: string): string {
  return `"${ident.replace(/"/g, '""')}"`;
}

export interface QueryPage {
  columns: string[];
  rows: unknown[][];
}

const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);
const MIN_SAFE = BigInt(Number.MIN_SAFE_INTEGER);

/** BigInt (DuckDB BIGINT/HUGEINT) can't be JSON.stringify'd directly.
 *  Downcast to Number when it fits without precision loss, otherwise
 *  stringify it rather than silently truncating. */
function toJsonSafe(value: unknown): unknown {
  if (typeof value === "bigint") {
    return value <= MAX_SAFE && value >= MIN_SAFE ? Number(value) : value.toString();
  }
  return value;
}

function rowsToJsonSafe(rows: unknown[][]): unknown[][] {
  return rows.map((row) => row.map(toJsonSafe));
}

/** Runs a parameterized SELECT and returns rows as plain JSON-safe objects.
 *  Used by the curated entity queries (players/teams/standings/draft/awards). */
export async function queryObjects<T = Record<string, unknown>>(
  sql: string,
  params?: DuckDBValue[],
): Promise<T[]> {
  const conn = await getConnection();
  const reader =
    params && params.length > 0
      ? await conn.runAndReadAll(sql, params)
      : await conn.runAndReadAll(sql);
  const rows = reader.getRowObjectsJson() as Record<string, unknown>[];
  return rows.map((row) => {
    const safe: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(row)) safe[key] = toJsonSafe(value);
    return safe as T;
  });
}

export async function readTablePage(
  table: TableInfo,
  limit: number,
  offset: number,
): Promise<QueryPage> {
  const conn = await getConnection();
  const sql = `SELECT * FROM ${quoteIdent(table.schema)}.${quoteIdent(table.name)} LIMIT ? OFFSET ?`;
  const reader = await conn.runAndReadAll(sql, [limit, offset]);
  return { columns: reader.columnNames(), rows: rowsToJsonSafe(reader.getRows()) };
}

/** Runs an arbitrary read-only query from the SQL box. The DuckDB
 *  connection itself is opened READ_ONLY, so DDL/DML is rejected by the
 *  engine regardless of what's typed here. */
export async function runReadOnlyQuery(sql: string): Promise<QueryPage> {
  const conn = await getConnection();
  const reader = await conn.runAndReadAll(sql);
  return { columns: reader.columnNames(), rows: rowsToJsonSafe(reader.getRows()) };
}
