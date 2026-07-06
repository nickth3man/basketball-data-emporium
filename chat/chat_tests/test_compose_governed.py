"""Unit tests for the governed-SQL composer (Phase 3 Lane B / Stage 3.3a).

`compose_governed` is the governed-SQL counterpart to the legacy
`compose`. These tests pin its dispatch behaviour and surface for the
Stage 3.3a gate so the pipeline (3.3b) can wire it next without
surprises. No DB, no agent -- we build a `QueryResult` matching the
shape `db.execute` returns and assert the contract.
"""

from __future__ import annotations

from typing import Literal

import pytest

from chat_server.agent import ResultContract
from chat_server.composer import Citation, compose_governed
from chat_server.db import QueryResult

# --- helpers -------------------------------------------------------------


def _result(
    rows: list[dict],
    *,
    columns: list[str] | None = None,
    row_count: int | None = None,
    truncated: bool = False,
) -> QueryResult:
    """Build a `QueryResult` matching the shape `db.execute` returns.

    Defaults match the cheap happy-path of the legacy composer tests:
    one fixed duration + ``truncated=False`` so tests don't depend on
    wall-clock timing. ``row_count`` defaults to ``len(rows)`` -- the
    production runner keeps the two in sync.
    """
    if rows and columns is None:
        columns = list(rows[0].keys())
    elif columns is None:
        columns = []
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=row_count if row_count is not None else len(rows),
        duration_ms=1.0,
        truncated=truncated,
    )


_SAMPLE_SQL = (
    "SELECT ps.full_name, ps.avg_pts "
    "FROM mart_player_season ps "
    "WHERE ps.season_year = '2015-16' "
    "ORDER BY ps.avg_pts DESC LIMIT 5"
)


# --- prose style ---------------------------------------------------------


def test_compose_governed_prose_default() -> None:
    """Prose style with a small result yields an answer that names the model."""
    contract = ResultContract(
        grain="one row per player",
        columns=["full_name", "avg_pts"],
        answer_style="prose",
    )
    rows = [
        {"full_name": "Stephen Curry", "avg_pts": 30.1},
        {"full_name": "Klay Thompson", "avg_pts": 22.1},
    ]
    result = _result(rows, columns=["full_name", "avg_pts"])

    composed = compose_governed(
        contract,
        result,
        _SAMPLE_SQL,
        model_name="player_season",
    )

    # Surface: stable ComposedAnswer shape.
    assert isinstance(composed.answer, str) and composed.answer
    assert composed.not_answerable is False
    # Model name shows up in the answer text (the prose formatter
    # wraps grain + "(from the {model} semantic model)").
    assert "player_season" in composed.answer
    # The grain echoes into the answer text so the user knows the shape.
    assert "one row per player" in composed.answer
    # Citations surface the model name as provenance.
    assert any(c.table_name == "player_season" for c in composed.citations), (
        f"expected a citation for the model, got {composed.citations}"
    )
    # Sanity: a Citation object, not a string.
    for c in composed.citations:
        assert isinstance(c, Citation)


# --- ranked_list style ---------------------------------------------------


def test_compose_governed_ranked_list_style() -> None:
    """`ranked_list` dispatches to the legacy ranked formatter."""
    contract = ResultContract(
        grain="top scorers",
        columns=["full_name", "season_year", "avg_pts"],
        answer_style="ranked_list",
    )
    rows = [
        {
            "full_name": "Stephen Curry",
            "season_year": "2015-16",
            "avg_pts": 30.1,
        },
        {
            "full_name": "Larry Bird",
            "season_year": "1987-88",
            "avg_pts": 29.9,
        },
    ]
    result = _result(rows)

    composed = compose_governed(contract, result, _SAMPLE_SQL)

    # The legacy ranked-list format starts with `<N> results for <title>`.
    # We passed grain="top scorers" so it should appear.
    assert "2 results for top scorers" in composed.answer
    assert "Stephen Curry" in composed.answer
    assert "30.1 PPG" in composed.answer
    assert "2015-16" in composed.answer


# --- empty rows defensive path ------------------------------------------


def test_compose_governed_empty_result_is_graceful() -> None:
    """Empty rows yield a graceful "no data" answer with no exception."""
    contract = ResultContract(
        grain="one row per player",
        columns=["full_name"],
        answer_style="prose",
    )
    result = _result([], columns=["full_name"])

    # The function must NOT raise -- the brief calls for a graceful
    # no-data path that mirrors the legacy composer's empty handling.
    composed = compose_governed(
        contract,
        result,
        _SAMPLE_SQL,
        model_name="player_season",
    )

    assert composed.not_answerable is False
    assert composed.answer, "empty result must still produce non-empty answer"
    # Both "No data" and "No rows" are reasonable mirrorings of the
    # legacy empty text -- accept either so we don't over-constrain.
    lowered = composed.answer.lower()
    assert "no data" in lowered or "no rows" in lowered, (
        f"expected a no-data acknowledgment, got {composed.answer!r}"
    )


# --- SQL provenance ------------------------------------------------------


def test_compose_governed_includes_sql_provenance() -> None:
    """The returned answer carries the SQL string as provenance.

    `ComposedAnswer` has no dedicated `sql` field -- the legacy
    `compose_not_answerable` records `attempted_sql` in
    `reasoning_summary`, so the governed composer follows the same
    convention. We assert the SQL string is present in
    `reasoning_summary` (subsequence match is fine: we prefix with a
    short 180-char slice and the test SQL is short enough to fit).
    """
    contract = ResultContract(
        grain="top scorers",
        columns=["full_name"],
        answer_style="prose",
    )
    rows = [{"full_name": "Stephen Curry"}]
    result = _result(rows)

    composed = compose_governed(
        contract,
        result,
        _SAMPLE_SQL,
        model_name="player_season",
    )

    assert composed.reasoning_summary is not None
    # Reasoning summary doubles as provenance: assert the SQL appears
    # (full or truncated prefix).
    assert "SELECT" in composed.reasoning_summary
    assert "mart_player_season" in composed.reasoning_summary
    # Also include the run-context fields so the audit trail is
    # uniform with the legacy composer.
    assert "style=prose" in composed.reasoning_summary
    assert "rows=1" in composed.reasoning_summary
    assert "model=player_season" in composed.reasoning_summary


# --- dispatch coverage (parametric) -------------------------------------


@pytest.mark.parametrize(
    ("style", "rows", "columns", "expect_substr"),
    [
        ("single_value", [{"answer_col": "forty-two"}], ["answer_col"], "forty-two"),
        ("count", [{"x": 1}, {"x": 2}, {"x": 3}], ["x"], "3 matching rows"),
        ("table", [{"a": 1, "b": 2}], ["a", "b"], "Table"),
    ],
)
def test_compose_governed_dispatch_matrix(
    style: Literal["single_value", "count", "table"],
    rows: list[dict],
    columns: list[str],
    expect_substr: str,
) -> None:
    """Each registered style renders a non-empty grounded answer."""
    contract = ResultContract(
        grain="test grain",
        columns=columns,
        answer_style=style,
    )
    result = _result(rows, columns=columns)

    composed = compose_governed(contract, result, _SAMPLE_SQL, model_name="player_season")

    assert composed.not_answerable is False
    assert composed.answer
    assert expect_substr in composed.answer
