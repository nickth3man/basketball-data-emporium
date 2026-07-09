"""Unit tests for `chat_server.json_safe`.

These are pure-Python tests — no DB connection required. They cover every
branch of `to_json_safe` plus the column/row zipping helper
`convert_rows`.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from chat_server.json_safe import SAFE_INT_MAX, convert_rows, to_json_safe

# ---------------------------------------------------------------------------
# to_json_safe — primitive passthrough
# ---------------------------------------------------------------------------


def test_none_passes_through() -> None:
    assert to_json_safe(None) is None


def test_bool_passes_through() -> None:
    assert to_json_safe(True) is True
    assert to_json_safe(False) is False


def test_str_passes_through() -> None:
    s = "hello world"
    assert to_json_safe(s) is s  # identity, no copy


def test_float_passes_through() -> None:
    f = 3.14159
    assert to_json_safe(f) is f


# ---------------------------------------------------------------------------
# to_json_safe — ints (HUGEINT safety)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [0, 1, -1, 42, -42, SAFE_INT_MAX, -SAFE_INT_MAX, 2_000_000_000],
)
def test_int_within_safe_range_passes_through(value: int) -> None:
    assert to_json_safe(value) == value
    assert isinstance(to_json_safe(value), int)


@pytest.mark.parametrize(
    "value",
    [SAFE_INT_MAX + 1, -(SAFE_INT_MAX + 1), 10**18, -(10**18), 2**63, -(2**63)],
)
def test_int_above_safe_range_becomes_str(value: int) -> None:
    out = to_json_safe(value)
    assert out == str(value)
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# to_json_safe — Decimal, datetime, timedelta, bytes
# ---------------------------------------------------------------------------


def test_decimal_becomes_float() -> None:
    # Decimal -> float (lossy is acceptable for stats).
    assert to_json_safe(Decimal("3.14")) == 3.14
    assert isinstance(to_json_safe(Decimal("3.14")), float)


def test_datetime_becomes_isoformat() -> None:
    dt = datetime.datetime(2024, 1, 2, 3, 4, 5, 123456)
    assert to_json_safe(dt) == "2024-01-02T03:04:05.123456"


def test_date_becomes_isoformat() -> None:
    d = datetime.date(1999, 12, 31)
    assert to_json_safe(d) == "1999-12-31"


def test_timedelta_becomes_total_seconds() -> None:
    td = datetime.timedelta(days=2, hours=3, minutes=4, seconds=5)
    # 2*86400 + 3*3600 + 4*60 + 5 = 183845
    assert to_json_safe(td) == 183845.0
    assert isinstance(to_json_safe(td), float)


def test_bytes_becomes_hex() -> None:
    raw = b"\x00\x01\xfe\xff"
    out = to_json_safe(raw)
    assert out == "0001feff"
    assert isinstance(out, str)


def test_bytearray_and_memoryview_become_hex() -> None:
    # Same payload as bytes test; verify the same conversion applies.
    expected = "0001feff"
    assert to_json_safe(bytearray(b"\x00\x01\xfe\xff")) == expected
    assert to_json_safe(memoryview(b"\x00\x01\xfe\xff")) == expected


# ---------------------------------------------------------------------------
# to_json_safe — collections, nesting
# ---------------------------------------------------------------------------


def test_list_recurses() -> None:
    out = to_json_safe([1, 2.5, "x", None, True])
    assert out == [1, 2.5, "x", None, True]


def test_list_with_hugeints_recurses() -> None:
    out = to_json_safe([SAFE_INT_MAX, SAFE_INT_MAX + 1])
    assert out == [SAFE_INT_MAX, str(SAFE_INT_MAX + 1)]


def test_tuple_recurses_to_list() -> None:
    out = to_json_safe((1, "a", None))
    assert out == [1, "a", None]
    assert isinstance(out, list)


def test_dict_recurses_and_stringifies_keys() -> None:
    out = to_json_safe({1: "a", "b": 2})
    # Non-string keys become strings (DuckDB STRUCT keys are always strings).
    assert out == {"1": "a", "b": 2}


def test_nested_struct_recurses() -> None:
    payload = {"outer": {"inner": [1, 2, {"deep": datetime.date(2024, 1, 1)}]}}
    assert to_json_safe(payload) == {
        "outer": {"inner": [1, 2, {"deep": "2024-01-01"}]},
    }


def test_empty_collections() -> None:
    assert to_json_safe([]) == []
    assert to_json_safe({}) == {}
    assert to_json_safe(()) == []


def test_unknown_object_falls_back_to_str() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird-value"

    assert to_json_safe(Weird()) == "weird-value"


# ---------------------------------------------------------------------------
# convert_rows — column/row zipping
# ---------------------------------------------------------------------------


def test_convert_rows_basic() -> None:
    columns = ["a", "b", "c"]
    raw = [(1, "x", None), (2, "y", True)]
    assert convert_rows(columns, raw) == [
        {"a": 1, "b": "x", "c": None},
        {"a": 2, "b": "y", "c": True},
    ]


def test_convert_rows_empty() -> None:
    assert convert_rows(["a", "b"], []) == []


def test_convert_rows_hugeint_and_datetime() -> None:
    columns = ["n", "d"]
    raw = [(SAFE_INT_MAX + 7, datetime.date(2024, 6, 15))]
    assert convert_rows(columns, raw) == [
        {"n": str(SAFE_INT_MAX + 7), "d": "2024-06-15"},
    ]


def test_convert_rows_zip_mismatch_short_row_pads_with_none() -> None:
    # DuckDB never returns short rows, but the helper must not blow up if
    # the caller hands one in (e.g. a misbehaving cursor). zip pads with
    # None for missing cells; we want the same behavior here.
    columns = ["a", "b", "c"]
    raw = [(1,)]
    assert convert_rows(columns, raw) == [{"a": 1, "b": None, "c": None}]
