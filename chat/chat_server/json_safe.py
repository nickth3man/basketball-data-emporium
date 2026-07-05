"""JSON-safe value conversion for DuckDB query results.

DuckDB's Python client returns Python-native types for every column: `int`
(HUGEINT/INTEGER/BIGINT/etc.), `float` (DOUBLE/REAL and DECIMAL in modern
releases), `Decimal` (DECIMAL when precision is configured), `datetime.date`
(DATE), `datetime.datetime` (TIMESTAMP/TIME), `timedelta` (INTERVAL),
`bytes` (BLOB), `str` (VARCHAR), `bool` (BOOLEAN), `list` (LIST/ARRAY), and
`dict` (STRUCT/MAP). `json.dumps` cannot serialize several of these.

This module provides a single recursive converter, `to_json_safe`, plus a
helper that zips a column list with a list of raw row tuples:
`convert_rows(columns, raw_rows) -> list[dict]`.

Per PLAN §7.2 and the conversation's verified warehouse facts:

* HUGEINT values that exceed `2 ** 53` (JavaScript's `Number.MAX_SAFE_INTEGER`)
  are downcast to `str` so they round-trip through JSON without silent
  truncation. Smaller HUGEINTs come back as plain Python `int`.
* Decimal is downcast to `float` (per the plan). Where column-level precision
  matters (rare; mostly large monetary / measurement fields), the renderer
  should `CAST(... AS VARCHAR)` upstream and ship a string.
* Datetimes and dates emit ISO 8601 strings.
* Timedeltas become total seconds (float).
* Bytes become hex strings (cheap, deterministic, JSON-safe).
* Lists, tuples, dicts, and structs recurse.

The converter is intentionally defensive: unknown object types fall through
to `str(value)` rather than raising, so a single odd cell never breaks an
otherwise good result page.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from itertools import zip_longest
from typing import Any

#: JavaScript's Number.MAX_SAFE_INTEGER. Anything larger cannot round-trip
#: through JSON without losing precision when re-parsed.
SAFE_INT_MAX = 2**53


def to_json_safe(value: Any) -> Any:
    """Recursively convert a DuckDB Python value to a JSON-serializable Python value.

    Mapping summary:

    * `None`, `bool`, `str`, `float` -> unchanged.
    * `int` -> `int` if `abs(value) <= SAFE_INT_MAX`, else `str(value)`.
    * `Decimal` -> `float`. Lossy for very large or high-precision decimals;
      templates that need exact precision should cast to VARCHAR in SQL.
    * `datetime.datetime`, `datetime.date` -> ISO 8601 string (`isoformat()`).
    * `datetime.timedelta` -> total seconds as a `float`.
    * `bytes` -> lowercase hex string.
    * `list`, `tuple` -> `list` of recursively converted items.
    * `dict` -> `dict` with string keys and recursively converted values
      (covers STRUCT and MAP from DuckDB).
    * anything else -> `str(value)` (defensive fallback).
    """
    if value is None or isinstance(value, (bool, str, float)):
        return value
    if isinstance(value, int):
        # HUGEINT/BIGINT/UBIGINT/INTEGER/SMALLINT/TINYINT all surface as `int`
        # in DuckDB's Python client. Python ints are unbounded, so we only
        # need to be careful about the JSON/JS precision boundary.
        return value if -SAFE_INT_MAX <= value <= SAFE_INT_MAX else str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        # Isoformat includes microseconds + timezone when present; json.dumps
        # encodes this verbatim.
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Hex is shorter and easier to scan than base64 for BLOB payloads of
        # the sizes the chatbot actually sees (UUIDs, hashes, etc.).
        return bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    # Defensive fallback: keep the result page from blowing up on one odd
    # cell (a custom Decimal subtype, a UUID, etc.). The downside is we
    # silently stringify unknown values; the upside is robustness.
    return str(value)


def convert_rows(columns: list[str], raw_rows: list[tuple]) -> list[dict[str, Any]]:
    """Zip `columns` with each `raw_rows` tuple, converting every cell.

    Parameters
    ----------
    columns
        Column names in the order DuckDB returned them. The length of this
        list must match the width of each row tuple; mismatches raise
        `ValueError` from the underlying `zip` (short rows are padded with
        `None`, long rows are truncated).
    raw_rows
        Row tuples as returned by `cursor.fetchall()`. May be empty.

    Returns
    -------
    list[dict]
        One dict per row, keys = column names, values = `to_json_safe` of
        the corresponding cell. Returns an empty list when `raw_rows` is
        empty.

    Notes
    -----
    Uses `itertools.zip_longest` so short rows are padded with `None`
    (defensive — DuckDB never produces short rows in practice, but a
    misbehaving cursor shouldn't crash the request). Long rows are
    truncated to the column list length.
    """
    if not raw_rows:
        return []
    return [
        {col: to_json_safe(cell) for col, cell in zip_longest(columns, row)} for row in raw_rows
    ]


__all__ = ["SAFE_INT_MAX", "to_json_safe", "convert_rows"]
