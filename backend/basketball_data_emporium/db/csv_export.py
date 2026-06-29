"""CSV export helpers."""

from __future__ import annotations

import csv
import io
from itertools import chain
from typing import Iterable, Iterator


def sanitize_csv_cell(value: object) -> object:
    """Return a spreadsheet-safe cell value."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def stream_csv_rows(rows: Iterable[dict[str, object]]) -> Iterable[bytes]:
    """Stream dictionaries as UTF-8 CSV bytes.

    The field order follows the first row. Prefer ``stream_csv_response`` when
    the API already has catalog column order available.
    """
    iterator = iter(rows)
    try:
        first = next(iterator)
    except StopIteration:
        return iter(())
    return stream_csv_response(first.keys(), chain([first], iterator))


def stream_csv_response(
    fieldnames: Iterable[str],
    rows: Iterable[dict[str, object]],
) -> Iterator[bytes]:
    """Yield CSV chunks with spreadsheet formula-injection protection."""
    fields = list(fieldnames)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")

    writer.writeheader()
    yield buffer.getvalue().encode("utf-8")
    buffer.seek(0)
    buffer.truncate(0)

    for row in rows:
        writer.writerow({key: sanitize_csv_cell(row.get(key)) for key in fields})
        yield buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)
