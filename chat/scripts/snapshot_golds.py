"""Snapshot ``gold_key_values`` from the live warehouse.

Reads every row of ``nba_chatbot_evals_v2.csv`` whose ``gold_sql`` is
populated, executes the SQL read-only against the warehouse, and
rewrites the row's ``gold_key_values`` cell in-place. The CSV is the
authoritative source of truth for gold; the orchestrator reviews the
diff before committing the rewrite.

Usage::

    uv run python scripts/snapshot_golds.py

Behaviour
---------
* Read-only against the warehouse (the singleton connection is opened
  with ``read_only=True`` -- enforced at the engine level, not just by
  convention).
* Idempotent: re-running overwrites the previous snapshot deterministically.
* On a parse / execute error the row's ``gold_key_values`` is left
  empty and the error is printed so the orchestrator can fix the
  ``gold_sql``.
* The CSV's column order is preserved (we rewrite via
  ``csv.DictReader`` + ``csv.DictWriter``) so the diff is one-line-per-row.

Snapshot format
---------------
A result set is flattened into a ``|``-joined list of normalized cell
strings, one per cell in row-major order. Numbers are stringified with
their natural repr; names are preserved as-is (the Layer-2 grader
normalizes case + whitespace at match time).
"""

from __future__ import annotations

import asyncio
import csv
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol, cast

# Repo root = parent of ``scripts/``. The CSV lives at
# ``<repo>/chat/plans/arch-overhaul/nba_chatbot_evals_v2.csv``.
_CSV_PATH = (
    Path(__file__).resolve().parent.parent / "plans" / "arch-overhaul" / "nba_chatbot_evals_v2.csv"
)


class _QueryResultLike(Protocol):
    columns: list[str]
    rows: list[dict]


def _stringify(value: object) -> str:
    """Render a single cell value as a normalized snapshot token.

    Empty / None collapses to ``""`` so a partial-result snapshot still
    reads cleanly. Non-string types use ``repr`` for round-trip safety
    (``Decimal('30.10')`` round-trips; ``str(30.1)`` would lose
    trailing zeros).
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return repr(value)


def _flatten_snapshot(columns: list[str], rows: list[Mapping[str, object]] | list[dict]) -> str:
    """Render one result set as a ``|``-joined snapshot string.

    Row-major, column-minor: the same order a human reads a table.
    Empty cells render as ``""`` (still surrounded by pipes) so the
    snapshot stays column-aligned when eyeballed.
    """
    tokens: list[str] = []
    for row in rows:
        for col in columns:
            tokens.append(_stringify(row.get(col)))
    return "|".join(tokens)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return ``(header, rows)`` from the CSV at ``path``."""
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    return header, rows


def _write_csv(path: Path, header: list[str], rows: Iterable[dict[str, str]]) -> None:
    """Rewrite the CSV at ``path`` preserving the original column order.

    When no row's ``gold_key_values`` actually changed (the typical
    re-run case where the warehouse hasn't been rebuilt), we skip the
    rewrite entirely so the diff stays empty -- the orchestrator
    expects to see only the rows that changed, never a wholesale
    line-ending rewrite of an otherwise-untouched CSV.
    """
    # ``csv.DictWriter`` materialises its input iterator once, so we
    # accept an Iterable without forcing the caller to materialise.
    rows_list = list(rows)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows_list)


def _execute_gold(db, sql: str) -> tuple[list[str], list[dict]] | None:
    """Execute ``sql`` against ``db``; return ``(columns, rows)`` or None on failure.

    The DB connection is read-only at the engine level (see
    ``chat_server.db.DuckDBSingleton``); a DDL/DML attempt will raise a
    ``duckdb.Error`` and we treat it as a snapshot failure.

    ``DuckDBSingleton.execute`` is async (it dispatches the sync work via
    ``asyncio.to_thread``); this helper awaits it, mirroring the pattern
    in ``evals.layer2.execute_plan_sql``.
    """
    try:
        coro = db.execute(sql)
        result = cast(
            _QueryResultLike,
            asyncio.run(coro) if asyncio.iscoroutine(coro) else coro,
        )
    except Exception as exc:  # noqa: BLE001 - surface to the orchestrator
        print(f"  ! execute failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    return list(result.columns), list(result.rows)


def _update_gold_row(row: dict[str, str], db, gold_sql: str) -> tuple[bool, bool]:
    """Update one CSV row and return ``(changed, snapshot_completed)``."""
    conversation_id = row.get("conversation_id", "")
    before = row.get("gold_key_values", "")
    result = _execute_gold(db, gold_sql)
    if result is None:
        row["gold_key_values"] = ""
        print(f"{conversation_id}: ERROR — gold_sql failed; gold_key_values left empty")
        return before != "", False

    columns, data_rows = result
    snapshot = _flatten_snapshot(columns, data_rows)
    row["gold_key_values"] = snapshot
    if before == snapshot:
        print(f"{conversation_id}: unchanged")
    elif before:
        print(f"{conversation_id}: before={before[:60]}... after={snapshot[:60]}...")
    else:
        print(f"{conversation_id}: NEW — {snapshot[:80]}...")
    return before != snapshot, True


def main(csv_path: Path | None = None) -> int:
    """Snapshot gold values into the CSV; return 0 on success, 1 on full failure."""
    path = Path(csv_path) if csv_path is not None else _CSV_PATH
    if not path.exists():
        print(f"CSV not found: {path}", file=sys.stderr)
        return 1
    header, rows = _read_csv(path)
    if "conversation_id" not in header or "gold_sql" not in header:
        print(f"CSV at {path} is missing required columns", file=sys.stderr)
        return 1

    # Lazy DB import: the snapshot script should be runnable from a
    # checkout that hasn't installed the warehouse dep yet (CI linting,
    # etc.). We only open the connection when a non-empty gold_sql is
    # actually present.
    db = None
    snapshots = 0
    changed = False
    for row in rows:
        gold_sql = (row.get("gold_sql") or "").strip()
        if not gold_sql:
            continue
        if db is None:
            from chat_server.db import get_db

            db = get_db()
        row_changed, completed = _update_gold_row(row, db, gold_sql)
        changed = changed or row_changed
        if completed:
            snapshots += 1

    # Only rewrite the CSV when something actually changed; otherwise
    # the rewrite would shuffle line endings on Windows and the diff
    # would balloon for an idempotent re-run.
    if changed:
        _write_csv(path, header, rows)
        print(f"snapshot complete: {snapshots} rows updated in {path}")
    else:
        print("snapshot complete: no changes (skipped rewrite)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
