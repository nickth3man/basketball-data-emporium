"""DuckDB read-only connection (Phase 0 minimal).

Phase 0 only needs a health-check connection. Phase 1 will replace this with
the full `DuckDBSingleton` async pool (`asyncio.to_thread` + `asyncio.Lock`,
see PLAN §7.2). The lazy singleton + `threading.Lock` here is intentionally
simple: enough to verify the warehouse is reachable from a `/api/health` call,
and trivially swappable for the Phase 1 implementation.
"""

from __future__ import annotations

import threading

import duckdb

from .config import get_settings

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None
_conn_error: Exception | None = None


def _open_connection() -> duckdb.DuckDBPyConnection:
    """Open a read-only DuckDB connection. Caller must hold `_lock`."""
    settings = get_settings()
    return duckdb.connect(settings.duckdb_path, read_only=True)


def check_connection() -> bool:
    """Open (or reuse) the read-only connection and run `SELECT 1`.

    Returns True on success, False on any `duckdb.Error`. Any other
    exception (e.g. a missing file path) is re-raised so the caller can
    distinguish "warehouse is broken" from "config is wrong".
    """
    global _conn, _conn_error
    with _lock:
        try:
            if _conn is None:
                _conn = _open_connection()
            _conn.execute("SELECT 1").fetchone()
            _conn_error = None
            return True
        except duckdb.Error as exc:
            _conn_error = exc
            return False


def get_db_path() -> str:
    """Return the configured DuckDB path (for diagnostics, never the conn)."""
    return get_settings().duckdb_path


def get_last_error() -> Exception | None:
    """Return the last `duckdb.Error` seen by `check_connection`, if any."""
    return _conn_error
