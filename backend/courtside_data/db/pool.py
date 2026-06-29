"""Read-only DuckDB connection pool.

The API is read-only and DuckDB connections are not safe for concurrent
queries on the same connection object, so we keep a small pool of
open `duckdb.DuckDBPyConnection` objects and hand them out one at a
time.

The pool is a process-wide singleton: opening the 22 GB DuckDB file
takes a non-trivial amount of time, and we want every uvicorn worker
process to share the same lifecycle (open on first use, never close
during the process's lifetime). The instance is held by
`@functtools.lru_cache(maxsize=1)` on `get_pool()` so the
`--reload` watcher re-uses the same connection objects within a
single worker process between reload cycles (the worker is respawned
by uvicorn on reload, so the cache naturally resets there).

DuckDB is opened with `read_only=True`. We *also* honor the
`DUCKDB_ACCESS_MODE` env var defensively — but as of DuckDB 1.4.x
the `access_mode` PRAGMA is locked once the database is attached,
so it must be set at `connect()` time. We therefore only honor
`READ_ONLY` here and treat any other value as a misconfiguration
that logs a warning and falls back to read-only.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)


def _resolve_duckdb_path() -> Path:
    """Resolve `DUCKDB_PATH` relative to the backend/ CWD."""
    raw = os.environ.get("DUCKDB_PATH", "../data/nba.duckdb")
    # The backend is always launched with CWD = backend/, so a relative
    # path like `../data/nba.duckdb` resolves correctly. `resolve()` makes
    # the path absolute for clearer error messages and stable file
    # handles.
    return Path(raw).resolve()


def _read_only() -> bool:
    """Honor DUCKDB_ACCESS_MODE defensively.

    We always open the database read-only — that is the architectural
    decision for the API. We accept the env var so deployments that
    set it to `READ_ONLY` get a clean log line, and any other value
    logs a warning (the connection is still opened read-only).
    """
    mode = os.environ.get("DUCKDB_ACCESS_MODE", "READ_ONLY").strip().upper()
    if mode == "READ_ONLY":
        return True
    logger.warning(
        "DUCKDB_ACCESS_MODE=%r is not supported by this service; "
        "the database will be opened READ_ONLY regardless.",
        mode,
    )
    return True


def _pool_size() -> int:
    raw = os.environ.get("DUCKDB_POOL_SIZE", "6").strip()
    try:
        size = int(raw)
    except ValueError:
        logger.warning("DUCKDB_POOL_SIZE=%r is not an int; falling back to 6.", raw)
        return 6
    if size < 1:
        logger.warning("DUCKDB_POOL_SIZE=%d is < 1; falling back to 1.", size)
        return 1
    if size > 32:
        logger.warning("DUCKDB_POOL_SIZE=%d is suspiciously high; clamping to 32.", size)
        return 32
    return size


class DuckDBPool:
    """A small fixed-size pool of read-only DuckDB connections.

    Connections are not safe to share between threads for concurrent
    queries, so `acquire()` blocks until a connection is free. The
    pool is process-wide; construct it once via `get_pool()` and
    share the instance.
    """

    def __init__(self, path: Path, size: int) -> None:
        self._path = path
        self._size = size
        self._lock = threading.Lock()
        # TODO P0-BE-02: this list alone is not enough to model a fixed-size
        # blocking pool. `acquire()` currently treats an empty available list as
        # "needs initialization", which can reopen connections when the pool is
        # saturated. Introduce a `threading.Condition`, track total opened
        # connections separately, and block/wake waiters on release.
        self._available: list[duckdb.DuckDBPyConnection] = []
        self._closed = False

    def _open_one(self) -> duckdb.DuckDBPyConnection:
        if not self._path.exists():
            raise FileNotFoundError(
                f"DuckDB file not found at {self._path} "
                f"(resolved from DUCKDB_PATH={os.environ.get('DUCKDB_PATH')!r})"
            )
        # `read_only=True` is the canonical read-only mechanism. The
        # `access_mode` PRAGMA is locked once the database is attached,
        # so we cannot re-issue it here — opening with `read_only=True`
        # is the only way.
        conn = duckdb.connect(str(self._path), read_only=True)
        # Sanity-check the connection with a trivial query so startup
        # fails loudly if the file is unreadable / locked by a writer.
        conn.execute("SELECT 1").fetchone()
        return conn

    def initialize(self) -> None:
        """Open every connection up front and validate them.

        Called once at app startup (or lazily on first `acquire()`).
        Raises if the file is missing or unreadable — we want a
        fail-fast crash, not silent 500s on every request.
        """
        with self._lock:
            if self._available:
                return
            logger.info(
                "Opening read-only DuckDB pool: path=%s size=%d",
                self._path,
                self._size,
            )
            try:
                for _ in range(self._size):
                    self._available.append(self._open_one())
            except Exception:
                # Close any half-opened connections before bubbling.
                for conn in self._available:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001 — best effort
                        pass
                self._available.clear()
                raise

    def acquire(self) -> duckdb.DuckDBPyConnection:
        if self._closed:
            raise RuntimeError("DuckDB pool is closed")
        if not self._available:
            # TODO P0-BE-02: once the pool has been initialized, an empty list
            # means "all connections are checked out", not "open a new batch".
            # Replace this lazy-init branch with condition-variable waiting and
            # a regression test that saturates a size-1 pool from two threads.
            # Lazy init so tests / scripts that don't call initialize()
            # explicitly still work.
            self.initialize()
        with self._lock:
            return self._available.pop()

    def release(self, conn: duckdb.DuckDBPyConnection) -> None:
        if self._closed:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return
        with self._lock:
            self._available.append(conn)

    @contextmanager
    def connection(self) -> Iterator[duckdb.DuckDBPyConnection]:
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    def close(self) -> None:
        with self._lock:
            for conn in self._available:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — best effort on shutdown
                    pass
            self._available.clear()
            self._closed = True


_pool_singleton: "Optional[DuckDBPool]" = None
_pool_lock = threading.Lock()


def get_pool() -> DuckDBPool:
    """Return the process-wide `DuckDBPool` singleton."""
    global _pool_singleton
    if _pool_singleton is None:
        with _pool_lock:
            if _pool_singleton is None:
                pool = DuckDBPool(path=_resolve_duckdb_path(), size=_pool_size())
                pool.initialize()
                _pool_singleton = pool
    return _pool_singleton


def get_db() -> Iterator[duckdb.DuckDBPyConnection]:
    """FastAPI dependency: yield a connection, return it on cleanup."""
    pool = get_pool()
    conn = pool.acquire()
    try:
        yield conn
    finally:
        pool.release(conn)
