"""Unit tests for the answer composer (no warehouse, no agent).

These tests cover the per-policy formatters, the empty-rows branch,
the not-answerable builder, and the citation surface. The composer is
pure (no I/O) so every test runs in isolation against an in-memory
``QueryResult`` constructed by hand.
"""

from __future__ import annotations

import pytest

from chat_server.composer import Citation, compose, compose_not_answerable
from chat_server.db import QueryResult
from chat_server.templates import Template

# --- helpers -------------------------------------------------------------


def _template(
    *,
    template_id: str = "season_thresholds.fifty_forty_ninety",
    title: str = "50-40-90 seasons with minimum PPG",
    answer_policy: str = "ranked_list",
    allowed_tables: set[str] | None = None,
) -> Template:
    """Construct a `Template` with just enough metadata for the composer.

    The composer only reads `template_id`, `title`, `answer_policy`, and
    `allowed_tables`; the other fields are filler so the dataclass
    constructor accepts the call.
    """
    return Template(
        template_id=template_id,
        title=title,
        description="test",
        sql="SELECT 1",
        # Stand-in class — composer never instantiates it; ``type(..., ...)``
        # is the lightest construction without pulling a real `BaseModel`.
        params_model=type("Params", (), {}),  # ty: ignore[invalid-argument-type]
        allowed_tables=allowed_tables or {"mart_player_season", "dim_player"},
        result_schema={},
        answer_policy=answer_policy,
        default_limit=50,
        timeout_seconds=30,
        examples=[],
        tests=[],
        capability="season_thresholds",
    )


def _result(rows: list[dict], *, columns: list[str] | None = None) -> QueryResult:
    """Construct a `QueryResult` for tests.

    Uses a single fixed duration + non-truncated flag so tests don't
    depend on wall-clock timing.
    """
    if rows and columns is None:
        columns = list(rows[0].keys())
    elif columns is None:
        columns = []
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        duration_ms=1.0,
        truncated=False,
    )


# --- ranked_list ---------------------------------------------------------


def test_ranked_list_top_scorer_appears_first():
    """The highest-PPG row appears first in the formatted answer."""
    template = _template()
    rows = [
        {
            "player_id": 1,
            "full_name": "Stephen Curry",
            "season_year": "2015-16",
            "fg_pct": 0.503,
            "fg3_pct": 0.454,
            "ft_pct": 0.908,
            "avg_pts": 30.1,
        },
        {
            "player_id": 2,
            "full_name": "Larry Bird",
            "season_year": "1987-88",
            "fg_pct": 0.525,
            "fg3_pct": 0.414,
            "ft_pct": 0.916,
            "avg_pts": 29.9,
        },
    ]
    result = _result(rows)

    composed = compose(template, result, template.template_id)

    assert "Stephen Curry" in composed.answer
    assert "30.1 PPG" in composed.answer
    assert "2015-16" in composed.answer
    # The composer truncates at 5 rows; with 2 rows there is no ellipsis.
    assert "…" not in composed.answer


def test_ranked_list_uses_ellipsis_when_more_than_five_rows():
    """Six rows → first five listed, ellipsis appended."""
    template = _template()
    rows = [
        {
            "full_name": f"Player {i}",
            "season_year": f"{2000 + i}-{(2001 + i) % 100:02d}",
            "avg_pts": 30.0 - i * 0.1,
        }
        for i in range(6)
    ]
    composed = compose(template, _result(rows), template.template_id)

    assert "Player 0" in composed.answer
    assert "Player 4" in composed.answer
    # The 6th row is truncated.
    assert "Player 5" not in composed.answer
    assert "…" in composed.answer


def test_ranked_list_handles_missing_season_and_pts_gracefully():
    """Rows without season or PPG degrade to a name-only entry."""
    template = _template()
    rows = [{"full_name": "Solo Name"}]
    composed = compose(template, _result(rows), template.template_id)
    assert "Solo Name" in composed.answer
    # No parentheses fragment when there is nothing to render.
    assert "()" not in composed.answer


# --- single_value --------------------------------------------------------


def test_single_value_formats_first_column_and_value():
    template = _template(answer_policy="single_value")
    rows = [{"answer_col": "forty-two"}]
    composed = compose(template, _result(rows, columns=["answer_col"]), template.template_id)
    assert composed.answer == "answer_col = forty-two"


# --- count ---------------------------------------------------------------


def test_count_policy_returns_row_count_text():
    template = _template(answer_policy="count")
    composed = compose(
        template,
        _result([{"x": 1}, {"x": 2}, {"x": 3}]),
        template.template_id,
    )
    assert composed.answer == "3 matching rows."


def test_count_policy_singular_for_one_row():
    template = _template(answer_policy="count")
    composed = compose(template, _result([{"x": 1}]), template.template_id)
    assert composed.answer == "1 matching row."


# --- empty / unknown policy ---------------------------------------------


def test_empty_rows_returns_graceful_text():
    template = _template()
    composed = compose(template, _result([], columns=["x"]), template.template_id)
    assert "No rows matched" in composed.answer


def test_unknown_policy_falls_back_to_generic_summary():
    template = _template(answer_policy="not_a_real_policy")
    rows = [{"col_a": 1, "col_b": 2}]
    composed = compose(template, _result(rows, columns=["col_a", "col_b"]), template.template_id)
    assert "Returned 1 row" in composed.answer
    assert "col_a" in composed.answer and "col_b" in composed.answer


def test_unknown_policy_handles_empty_rows():
    """Empty rows yield the policy-agnostic empty message regardless of policy.

    The composer's empty branch is centralised in ``_format_empty`` so the
    answer text is identical for ranked_list / single_value / count /
    unknown policies — only the *populated* path branches on policy.
    """
    template = _template(answer_policy="not_a_real_policy")
    composed = compose(template, _result([], columns=["x"]), template.template_id)
    assert composed.answer == "No rows matched the query."


# --- citations -----------------------------------------------------------


def test_compose_emits_one_citation_per_allowed_table():
    template = _template(
        allowed_tables={"mart_player_season", "dim_player"},
    )
    composed = compose(
        template, _result([{"full_name": "x", "avg_pts": 1.0}]), template.template_id
    )
    names = {c.table_name for c in composed.citations}
    assert names == {"mart_player_season", "dim_player"}
    # Phase 3: no metric/gap citations.
    for c in composed.citations:
        assert c.metric_key is None
        assert c.gap_key is None


def test_compose_citations_are_sorted_for_determinism():
    template = _template(
        allowed_tables={"zeta", "alpha", "mu"},
    )
    composed = compose(template, _result([]), template.template_id)
    assert [c.table_name for c in composed.citations] == ["alpha", "mu", "zeta"]


def test_compose_reasoning_summary_describes_template_and_policy():
    template = _template(answer_policy="ranked_list")
    rows = [{"full_name": "x", "avg_pts": 1.0}]
    result = _result(rows)
    composed = compose(template, result, template.template_id)
    assert composed.reasoning_summary is not None
    assert "template=" in composed.reasoning_summary
    assert "policy=ranked_list" in composed.reasoning_summary
    assert "rows=1" in composed.reasoning_summary


# --- not-answerable ------------------------------------------------------


def test_compose_not_answerable_sets_flag_and_mirrors_note():
    composed = compose_not_answerable("no template fits")
    assert composed.not_answerable is True
    assert composed.not_answerable_note == "no template fits"
    # Answer and note are intentionally identical so the persisted
    # JSONL history carries the full message.
    assert composed.answer == "no template fits"
    assert composed.citations == []


def test_compose_not_answerable_attaches_sql_to_reasoning_summary():
    sql = "SELECT 1 FROM mart_player_season"
    composed = compose_not_answerable("params invalid", attempted_sql=sql)
    assert composed.reasoning_summary is not None
    assert sql in composed.reasoning_summary
    assert "params invalid" in composed.reasoning_summary


def test_compose_not_answerable_without_sql_leaves_reasoning_none():
    composed = compose_not_answerable("no template fits")
    assert composed.reasoning_summary is None


# --- citation dataclass invariants --------------------------------------


def test_citation_defaults_are_none():
    """`Citation` is a value object; defaults make both optional axes usable."""
    c = Citation()
    assert c.table_name is None
    assert c.metric_key is None
    assert c.gap_key is None


@pytest.mark.parametrize(
    ("policy", "rows", "expected_substr"),
    [
        ("ranked_list", [{"full_name": "A", "avg_pts": 1.0}], "A"),
        ("single_value", [{"v": 42}], "42"),
        ("count", [{"x": 1}, {"x": 2}], "2 matching rows"),
    ],
)
def test_compose_dispatch_matrix(policy: str, rows: list[dict], expected_substr: str):
    """Each registered policy renders a non-empty grounded answer."""
    template = _template(answer_policy=policy)
    composed = compose(template, _result(rows), template.template_id)
    assert composed.answer
    assert expected_substr in composed.answer
    assert composed.not_answerable is False
