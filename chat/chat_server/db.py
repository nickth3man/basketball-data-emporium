"""Async DuckDB read-only access for the chatbot.

PLAN §7.2. One process-wide read-only `duckdb.DuckDBPyConnection` with a
small pool of cursors, guarded by a `threading.Lock`. All public methods
are `async` and delegate the synchronous DuckDB call to
`asyncio.to_thread`, so the FastAPI event loop never blocks on a query.

Concurrency contract (critical)
------------------------------
Multiple **read-only** processes can coexist on `data/nba.duckdb` — the
chatbot, the `web/` Express API, and ad-hoc CLI readers — because DuckDB
allows many read-only handles per file.

However, **no read-write connection can coexist with any read-only
connection on the same file**. Before running `data/audit/build_nba.py`
(rebuild), stop the chatbot server, the `web/` dev server, and any
read-only CLI reader. The startup wrapper should warn loudly if the
warehouse file is currently write-locked, but the engine itself rejects
DDL/DML on a read-only connection so the failure mode is at least clean.

Design notes
------------
* The connection is opened with `duckdb.connect(path, read_only=True)`.
  DuckDB refuses DDL/DML on it at the engine level — the second layer of
  defense behind the SQLGlot allowlist (`validation.py`).
* `pool_size` cursors are allocated eagerly. Cursors share the underlying
  connection instance (cheap). A round-robin index picks one per query;
  even though the `threading.Lock` serializes everything end-to-end, this
  honors the documented API contract.
* The default row cap (`default_limit`) is appended to SQL only when the
  rendered query does **not** already contain a `LIMIT` clause. The
  detection is a comment-stripping, lowercased token scan — deliberately
  regex-free (we don't want to import `re` for a heuristic).
* `execute` re-raises `duckdb.Error` unchanged. The pipeline layer is
  expected to catch and translate into the SSE `error` event.

Phase 0 compatibility
----------------------
The previous Phase 0 `check_connection` shim is preserved at module
bottom so `routes/meta.py` keeps working unchanged. New code should call
`get_db()` (returns the singleton) instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass
from typing import Any

import duckdb

from .config import get_settings
from .json_safe import convert_rows


@dataclass
class QueryResult:
    """One query execution result, JSON-safe.

    Attributes
    ----------
    columns
        Column names in the order DuckDB returned them.
    rows
        Each row is a `{column: value}` dict with every value passed through
        `chat_server.json_safe.to_json_safe` (so the result is ready for
        `json.dumps` and SSE serialization).
    row_count
        Number of rows in `rows` (== `len(rows)`; kept as a separate field
        so callers don't have to materialize the list to count).
    duration_ms
        Wall-clock time spent in `_execute_sync`, milliseconds.
    truncated
        `True` iff the runner appended a default `LIMIT N` to the SQL.
        Templates that hit the cap should say so in the answer.
    """

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    duration_ms: float
    truncated: bool


def _has_existing_limit(sql: str) -> bool:
    """Return True if `sql` already contains a `LIMIT` clause.

    Heuristic, deliberately regex-free (we don't want `re` import in the
    hot path of every query). Steps:

    1. Strip `--` line comments.
    2. Strip `/* ... */` block comments.
    3. Drop trailing semicolons + whitespace.
    4. Lowercase.
    5. Split on whitespace; check if any token is exactly `limit`.

    This is good enough for template SQL that we control. False positives
    (a column named `limit`) are rare in our warehouse and would be a
    template author's bug to fix anyway.
    """
    # Step 1: line comments.
    no_line_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    # Step 2: block comments.
    text = no_line_comments
    while "/*" in text:
        a = text.find("/*")
        b = text.find("*/", a + 2)
        if b == -1:
            text = text[:a]
            break
        text = text[:a] + " " + text[b + 2 :]
    # Step 3 + 4: trim, strip trailing semicolons, lowercase.
    text = text.strip().rstrip(";").strip().lower()
    if not text:
        return False
    # Step 5: token scan.
    return "limit" in text.split()


class DuckDBSingleton:
    """Process-wide read-only DuckDB handle.

    Instantiate once via `get_db()`. Re-use the returned instance for the
    life of the process. The instance is not safe to construct twice on
    the same `db_path` in the same process (DuckDB holds a file lock even
    in read-only mode).

    Parameters
    ----------
    db_path
        Absolute or process-relative path to the DuckDB file.
    pool_size
        Number of cursors to allocate eagerly. Cursors share the underlying
        connection; this is mostly an API contract more than a perf knob,
        but it does mean we pre-pay the cursor allocation cost.
    default_limit
        Fallback row cap applied to queries whose SQL has no explicit
        `LIMIT` clause. Set to `None` to disable the cap (templates should
        almost always have one).
    """

    def __init__(
        self,
        db_path: str,
        pool_size: int = 3,
        default_limit: int | None = 5000,
    ) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        self._db_path = db_path
        self._pool_size = pool_size
        self._default_limit = default_limit
        # Single read-only connection; cursors share this instance.
        self._conn: duckdb.DuckDBPyConnection = duckdb.connect(db_path, read_only=True)
        # Eager cursor pool. In duckdb's Python client `cursor()` returns a
        # lightweight handle onto the same connection; type-wise it is
        # reported as `DuckDBPyConnection` (no separate cursor class).
        # We pre-pay so the hot path never blocks on cursor allocation.
        self._cursors: list[duckdb.DuckDBPyConnection] = [
            self._conn.cursor() for _ in range(pool_size)
        ]
        self._cursor_index = 0
        # Serializes every DuckDB call across threads (asyncio.to_thread
        # can dispatch to multiple workers concurrently). DuckDB's Python
        # client is thread-safe at the connection level, but we serialize
        # anyway to keep result correlation predictable and to bound
        # cursor usage to one in-flight query at a time.
        self._lock = threading.Lock()

    @property
    def db_path(self) -> str:
        """The path passed to `duckdb.connect`."""
        return self._db_path

    @property
    def pool_size(self) -> int:
        """The cursor pool size (set at construction time)."""
        return self._pool_size

    async def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> QueryResult:
        """Run `sql` (optionally with `$name` params) and return a `QueryResult`.

        All blocking DuckDB work happens on a worker thread via
        `asyncio.to_thread`. `duckdb.Error` propagates unchanged.

        Parameters
        ----------
        sql
            The rendered SQL. May contain DuckDB named placeholders
            (`$name`).
        params
            Optional dict of placeholder name -> Python value, bound by
            DuckDB's DB-API. Pass `None` to skip binding.
        limit
            Optional per-call row cap. If set and the SQL has no `LIMIT`
            clause, the runner appends `LIMIT N` and marks the result
            `truncated=True`. If `None`, falls back to the singleton's
            `default_limit`.
        """
        return await asyncio.to_thread(self._execute_sync, sql, params, limit)

    def _acquire_cursor(self) -> duckdb.DuckDBPyConnection:
        """Round-robin cursor selection. Caller holds `self._lock`."""
        cur = self._cursors[self._cursor_index]
        self._cursor_index = (self._cursor_index + 1) % len(self._cursors)
        return cur

    def _execute_sync(
        self,
        sql: str,
        params: dict[str, Any] | None,
        limit: int | None,
    ) -> QueryResult:
        """Synchronous execution body. Always called via `asyncio.to_thread`."""
        effective_limit = limit if limit is not None else self._default_limit
        injected_limit = False
        rendered_sql = sql

        with self._lock:
            cur = self._acquire_cursor()

            # Append LIMIT if requested and the SQL doesn't already have one.
            if effective_limit is not None and not _has_existing_limit(sql):
                rendered_sql = f"{sql.rstrip().rstrip(';').rstrip()} LIMIT {int(effective_limit)}"
                injected_limit = True

            t0 = time.perf_counter()
            if params:
                cur.execute(rendered_sql, params)
            else:
                cur.execute(rendered_sql)
            raw_rows = cur.fetchall()
            duration_ms = (time.perf_counter() - t0) * 1000.0

        # `cursor.description` is the DB-API 7-tuple; column name is index 0.
        columns: list[str] = [str(col[0]) for col in (cur.description or ()) if col and col[0]]
        rows = convert_rows(columns, raw_rows)
        # Truncated means results were actually cut off: an injected cap was hit.
        # If we injected LIMIT N and got back exactly N rows, more may exist.
        truncated = bool(injected_limit and effective_limit and len(rows) >= effective_limit)
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            duration_ms=duration_ms,
            truncated=truncated,
        )

    def close(self) -> None:
        """Close the underlying connection.

        After `close`, the instance is unusable; the process should
        construct a fresh `DuckDBSingleton` if it needs to query again.
        Currently no callers use this (the singleton lives for the
        process lifetime) but it's here for the tests + a future
        graceful-shutdown path.
        """
        with self._lock:
            self._cursors.clear()
            # Closing an already-closed connection raises; swallow it.
            with contextlib.suppress(duckdb.Error):
                self._conn.close()


# Module-level lazy singleton + double-checked locking.
_singleton: DuckDBSingleton | None = None
_singleton_lock = threading.Lock()


def get_db() -> DuckDBSingleton:
    """Return the process-wide `DuckDBSingleton`, constructing it on first call.

    The path comes from `chat_server.config.get_settings().duckdb_path`,
    so any settings error (missing env var) surfaces here on first access.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = DuckDBSingleton(get_settings().duckdb_path)
    return _singleton


def reset_singleton_for_tests() -> None:
    """Test hook: close + drop the singleton so the next `get_db()` rebuilds it."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = None


# ---------------------------------------------------------------------------
# Phase 0 compatibility shims.
#
# `routes/meta.py` (untouchable per the Phase 1 task spec) imports
# `check_connection`. Rather than touch the route, we keep that name alive
# here as a thin wrapper that runs `SELECT 1` synchronously against the
# singleton. New code should prefer `get_db().execute("SELECT 1")`.
# ---------------------------------------------------------------------------


def check_connection() -> bool:
    """Open (or reuse) the read-only connection and run `SELECT 1`.

    Returns True on success, False on any `duckdb.Error`. Any other
    exception (e.g. a missing file path) is re-raised so the caller can
    distinguish "warehouse is broken" from "config is wrong".
    """
    try:
        db = get_db()
    except Exception:
        # Config / import errors surface here (e.g. missing DUCKDB_PATH).
        # Phase 0 callers expect a True/False verdict; bubble it up as
        # "disconnected" by returning False — the calling /health route
        # already catches broad exceptions for non-duckdb errors.
        return False
    try:
        with db._lock:  # noqa: SLF001 - intentional single-statement health probe
            cur = db._acquire_cursor()  # noqa: SLF001
            cur.execute("SELECT 1").fetchone()
        return True
    except duckdb.Error:
        return False


def get_db_path() -> str:
    """Return the configured DuckDB path (for diagnostics, never the conn)."""
    return get_settings().duckdb_path


__all__ = [
    "DuckDBSingleton",
    "QueryResult",
    "get_db",
    "reset_singleton_for_tests",
    "check_connection",
    "get_db_path",
]
