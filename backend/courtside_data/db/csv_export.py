"""CSV export scaffold."""

from __future__ import annotations

from typing import Iterable


def sanitize_csv_cell(value: object) -> object:
    """Return a spreadsheet-safe cell value."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def stream_csv_rows(rows: Iterable[dict[str, object]]) -> Iterable[bytes]:
    """Placeholder for chunked CSV streaming.

    TODO P2-BE-04: Stream CSV exports.
    Current route code materializes CSV into `StringIO`. Replace it with a
    streaming response that can handle large datasets, ideally using Arrow/CSV
    chunks from DuckDB while preserving formula-injection protection.
    """
    _ = rows
    return ()

