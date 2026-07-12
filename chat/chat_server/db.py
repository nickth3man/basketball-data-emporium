"""Async DuckDB read-only access for the chatbot.

One process-wide read-only `duckdb.DuckDBPyConnection` with a
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


class QueryTimeoutError(Exception):
    """Raised by :meth:`DuckDBSingleton.execute` when a query exceeds its
    wall-clock budget and is interrupted.

    The gate validates *correctness*, not *cost*: an expensive-but-valid
    query passes every validation layer, so the bound has to be a runtime
    mechanism. A watchdog timer calls ``interrupt()`` on the executing
    cursor once the budget elapses; the resulting engine error is
    translated into this exception so callers can branch on it without
    importing ``duckdb``.

    Attributes
    ----------
    sql
        The SQL string that was interrupted.
    timeout_seconds
        The wall-clock budget that was exceeded.
    """

    def __init__(self, sql: str, timeout_seconds: float) -> None:
        super().__init__(f"query exceeded {timeout_seconds:g}s timeout")
        self.sql = sql
        self.timeout_seconds = timeout_seconds


class DryRunError(Exception):
    """Raised by :meth:`DuckDBSingleton.dry_run` when the warehouse rejects a
    query at plan / bind time (catalog resolution, unknown column, ambiguous
    reference, parse error, ...).

    The original SQL and the underlying ``duckdb.Error`` are both preserved
    so the repair loop (see :mod:`chat_server.repair`) can show the model
    exactly which SQL failed and what DuckDB complained about.

    Attributes
    ----------
    sql
        The SQL string that failed to dry-run.
    original
        The underlying ``duckdb.Error`` raised by the engine. ``str(exc)``
        in the repair-prompt context should match what DuckDB printed.
    """

    def __init__(self, sql: str, original: duckdb.Error) -> None:
        super().__init__(f"dry-run failed: {original}")
        self.sql = sql
        self.original = original


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
    no_line_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    text = no_line_comments
    while "/*" in text:
        a = text.find("/*")
        b = text.find("*/", a + 2)
        if b == -1:
            text = text[:a]
            break
        text = text[:a] + " " + text[b + 2 :]
    text = text.strip().rstrip(";").strip().lower()
    if not text:
        return False
    return "limit" in text.split()


def _apply_row_limit(sql: str, limit: int | None) -> tuple[str, bool]:
    """Return SQL with the configured cap appended when it has no LIMIT."""
    if limit is None or _has_existing_limit(sql):
        return sql, False
    rendered = f"{sql.rstrip().rstrip(';').rstrip()} LIMIT {int(limit)}"
    return rendered, True


def _start_watchdog(
    cursor: duckdb.DuckDBPyConnection,
    timeout_seconds: float | None,
) -> tuple[threading.Event, threading.Timer | None]:
    fired = threading.Event()
    if timeout_seconds is None:
        return fired, None

    def interrupt() -> None:
        fired.set()
        with contextlib.suppress(Exception):
            cursor.interrupt()

    timer = threading.Timer(timeout_seconds, interrupt)
    timer.daemon = True
    timer.start()
    return fired, timer


def _execute_cursor(
    cursor: duckdb.DuckDBPyConnection,
    rendered_sql: str,
    params: dict[str, Any] | None,
    *,
    original_sql: str,
    timeout_seconds: float | None,
) -> tuple[list[tuple], float]:
    """Execute one query and translate watchdog interrupts into a timeout."""
    fired, timer = _start_watchdog(cursor, timeout_seconds)
    started_at = time.perf_counter()
    try:
        if params:
            cursor.execute(rendered_sql, params)
        else:
            cursor.execute(rendered_sql)
        rows = cursor.fetchall()
    except duckdb.Error as exc:
        if fired.is_set() or isinstance(exc, duckdb.InterruptException):
            raise QueryTimeoutError(
                sql=original_sql,
                timeout_seconds=float(timeout_seconds or 0),
            ) from None
        raise
    finally:
        if timer is not None:
            timer.cancel()
    return rows, (time.perf_counter() - started_at) * 1000.0


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
    memory_limit
        Optional DuckDB ``memory_limit`` value (e.g. ``"8GB"``) applied via
        ``SET`` right after connect, sized to leave the host usable if a
        runaway query slips past the gate. ``None`` keeps DuckDB's default.
    """

    def __init__(
        self,
        db_path: str,
        pool_size: int = 3,
        default_limit: int | None = 5000,
        memory_limit: str | None = None,
    ) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        self._db_path = db_path
        self._pool_size = pool_size
        self._default_limit = default_limit
        self._conn: duckdb.DuckDBPyConnection = duckdb.connect(db_path, read_only=True)
        if memory_limit:
            # Session setting; legal on a read-only connection. The value is
            # operator config, never user input — safe to inline.
            self._conn.execute(f"SET memory_limit = '{memory_limit}'")
        self._cursors: list[duckdb.DuckDBPyConnection] = [
            self._conn.cursor() for _ in range(pool_size)
        ]
        self._cursor_index = 0
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
        timeout_seconds: float | None = None,
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
        timeout_seconds
            Optional wall-clock budget. When set, a watchdog timer calls
            ``interrupt()`` on the executing cursor once the budget
            elapses and the call raises :class:`QueryTimeoutError`.
            ``None`` (the default) runs unbounded — internal callers
            (lookups, schema introspection) stay untouched.
        """
        return await asyncio.to_thread(self._execute_sync, sql, params, limit, timeout_seconds)

    async def dry_run(self, sql: str) -> None:
        """Validate ``sql`` resolves cleanly without executing it.

        Implementation
        --------------
        We wrap ``sql`` as ``EXPLAIN <sql>`` and run it through the same
        ``_execute_sync`` / lock path that :meth:`execute` uses. DuckDB's
        ``EXPLAIN`` walks the planner end-to-end (bind columns, resolve
        types, build the logical plan) without ever touching the row
        data — exactly the dry-run semantics we want. Any schema /
        column / type / parse problem surfaces here as a
        :class:`duckdb.Error`, which we re-raise as :class:`DryRunError`
        so callers can branch on it without importing ``duckdb``.

        Returns
        -------
        None
            On success; the warehouse accepted the plan.

        Raises
        ------
        DryRunError
            If ``sql`` failed to bind / parse / resolve against the live
            schema. The original SQL and the underlying ``duckdb.Error``
            are preserved on the exception instance.

        Concurrency
        -----------
        Reuses the same ``self._lock`` that ``execute`` holds, so a
        dry-run and a live execute serialize against each other exactly
        the same way two live executes would. We deliberately do NOT
        introduce a second lock — the duckdb connection allows only one
        cursor to drive it at a time, and ``EXPLAIN`` is a read-only
        operation that costs nothing relative to the queries it
        precedes.
        """
        await asyncio.to_thread(self._dry_run_sync, sql)

    def _dry_run_sync(self, sql: str) -> None:
        """Synchronous body for :meth:`dry_run`.

        Always called via :func:`asyncio.to_thread`. Holds ``self._lock``
        around the cursor acquisition + ``EXPLAIN`` execution so the
        dry-run serializes against ``execute`` calls. Catches
        ``duckdb.Error`` and re-raises as :class:`DryRunError` carrying
        both the SQL and the original exception.
        """
        with self._lock:
            cur = self._acquire_cursor()
            try:
                cur.execute(f"EXPLAIN {sql}")
            except duckdb.Error as exc:
                raise DryRunError(sql=sql, original=exc) from None
            cur.fetchall()

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
        timeout_seconds: float | None = None,
    ) -> QueryResult:
        """Synchronous execution body. Always called via `asyncio.to_thread`."""
        effective_limit = limit if limit is not None else self._default_limit
        rendered_sql, injected_limit = _apply_row_limit(sql, effective_limit)

        with self._lock:
            cur = self._acquire_cursor()
            raw_rows, duration_ms = _execute_cursor(
                cur,
                rendered_sql,
                params,
                original_sql=sql,
                timeout_seconds=timeout_seconds,
            )

        columns: list[str] = [str(col[0]) for col in (cur.description or ()) if col and col[0]]
        rows = convert_rows(columns, raw_rows)
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
            with contextlib.suppress(duckdb.Error):
                self._conn.close()


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
            settings = get_settings()
            _singleton = DuckDBSingleton(
                settings.duckdb_path,
                memory_limit=settings.chat_memory_limit,
            )
    return _singleton


def reset_singleton_for_tests() -> None:
    """Test hook: close + drop the singleton so the next `get_db()` rebuilds it."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = None


def check_connection() -> bool:
    """Open (or reuse) the read-only connection and run `SELECT 1`.

    Returns True on success, False on any `duckdb.Error`. Any other
    exception (e.g. a missing file path) is re-raised so the caller can
    distinguish "warehouse is broken" from "config is wrong".
    """
    try:
        db = get_db()
    except Exception:
        return False
    try:
        with db._lock:  # noqa: SLF001
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
    "DryRunError",
    "QueryTimeoutError",
    "get_db",
    "reset_singleton_for_tests",
    "check_connection",
    "get_db_path",
]
