// Resolves the local DuckDB warehouse path the same way `web/server/db.ts`
// does (env override > default) so the test suite and the API agree on
// which file they're looking at. `__dirname` here is `web/test/`, so
// `../../data/nba.duckdb` lands at `<repo-root>/data/nba.duckdb`.
//
// We never open the connection here — `web/server/queries.ts` and
// `web/server/db.ts::queryObjects` already own a single READ_ONLY
// singleton. The suite only needs to know whether the file is on disk
// locally; when it isn't (CI), the whole data-hardening suite skips.
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const DB_PATH = process.env.DUCKDB_PATH ?? path.resolve(__dirname, "../../data/nba.duckdb");

export const DB_AVAILABLE = existsSync(DB_PATH);
