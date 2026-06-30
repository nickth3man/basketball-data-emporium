"""Shared fixtures for the verification (Layer 5) suite.

The reconciliation tests in this package fall into two camps:

* **Offline / plumbing tests** — these exercise ``reconcile()``,
  the column maps, the ``OfficialFetcher`` retry/classification
  helpers, and the ``FakeFetcher``. They do not touch the
  warehouse, do not need nba_api, and run on any CI runner
  including hosted GitHub Actions.
* **DB-dependent tests** — the two ``TestExitCodePolicy`` tests
  that drive the ``--run`` path against ``unified_star.dim_player``
  need a read-only DuckDB handle to look up the first warehouse
  row (so they can plant a known-bad / known-good official row to
  prove the exit-code policy).

The DB-dependent tests must SKIP cleanly on hosted CI (where the
22 GB ``nba.duckdb`` snapshot is absent) so the suite stays green
on runners that do not carry the snapshot. This conftest mirrors
the convention already used by :mod:`tests.invariant.conftest` and
:mod:`tests.schema.conftest`: resolve the snapshot path from
``BASKETBALL_DATA_DB_PATH`` / ``DUCKDB_PATH`` / the
``../data/nba.duckdb`` default, and ``pytest.skip()`` with a clear
message if the file is missing.

Why a fresh per-test connection (not session-scoped)
----------------------------------------------------

The invariant conftest uses a session-scoped ``db`` fixture because
opening the 22 GB file is expensive and the invariant suite runs
hundreds of small queries. The verification suite has only two
DB-dependent tests; per-test connections keep the fixture simple
and avoid leaking state between tests (one of them uses
``monkeypatch.setattr`` to swap the fetcher factory, and an open
cursor would be a needless liability). The ~1 s open cost is
acceptable here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest


def _resolve_db_path() -> Path:
    """Resolve the DuckDB snapshot path.

    Mirrors :func:`tests.invariant.conftest._resolve_db_path`:
    ``BASKETBALL_DATA_DB_PATH`` wins, then ``DUCKDB_PATH``, then the
    ``../data/nba.duckdb`` default (relative to the ``backend/`` CWD).
    """
    raw = (
        os.environ.get("BASKETBALL_DATA_DB_PATH")
        or os.environ.get("DUCKDB_PATH")
        or "../data/nba.duckdb"
    )
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


@pytest.fixture()
def duckdb_conn() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a read-only DuckDB connection for a single test.

    Skips the test cleanly when the snapshot is absent so hosted
    CI (which does not mount the 22 GB file) does not error.
    """
    path = _resolve_db_path()
    if not path.exists():
        pytest.skip(
            f"DuckDB snapshot not found at {path}; "
            "set DUCKDB_PATH (or BASKETBALL_DATA_DB_PATH) to enable "
            "DB-dependent verification tests."
        )
    conn = duckdb.connect(str(path), read_only=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def first_dim_player_row(
    duckdb_conn: duckdb.DuckDBPyConnection,
) -> tuple[int, str, bool, int, int]:
    """Return the first ``unified_star.dim_player`` row (by player_id).

    Tests that need to plant a known-bad / known-good official row
    for the ``--run`` path can read the warehouse's first row once,
    then mutate a single field to drive the desired exit code. The
    fixture inherits the skip-when-absent behavior from
    :func:`duckdb_conn`.
    """
    row: Any = duckdb_conn.execute(
        "SELECT player_id, full_name, is_active, from_year, to_year "
        "FROM unified_star.dim_player ORDER BY player_id LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip(
            "unified_star.dim_player is empty; "
            "DB-dependent verification tests cannot run."
        )
    return (int(row[0]), str(row[1]), bool(row[2]), int(row[3]), int(row[4]))
