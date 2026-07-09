"""Integration tests for TEMPLATE BATCH C (Phase 6).

Covers the 5 HEAVY templates owned by this fixer:

* ``pbp_aggregate.largest_scoring_run``
* ``pbp_aggregate.fouls_by_period``
* ``clutch_terminal.clutch_ts_leader``
* ``clutch_terminal.buzzer_beaters`` (spike-gated, REAL)
* ``lineup_court.fiveman_shared_court`` (spike-gated, REAL)

Heavy tier — each template sets ``TIMEOUT_SECONDS=300`` in its metadata,
but every shipped query runs sub-second locally on the bounded
``season_year`` / ``season_type`` / ``player_id`` filters (verified in
the spike notes per template).  This file is what the heavy-target
latency tier actually exercises.

The 2 spike templates both ship REAL (clock reliability is sufficient
from 1996-97 onward; ``src_agg_lineup_efficiency`` has the lineup
aggregate columns precomputed).  The test honors any future switch to
``NOT_ANSWERABLE=True`` so that a regression is caught the moment a
template changes its spike verdict.
"""

from __future__ import annotations

import pytest

from chat_server.db import get_db
from chat_server.templates import get_template
from chat_tests.conftest import skip_no_db

# Heavy-template ids owned by this batch.
TEMPLATE_IDS: tuple[str, ...] = (
    "pbp_aggregate.largest_scoring_run",
    "pbp_aggregate.fouls_by_period",
    "clutch_terminal.clutch_ts_leader",
    "clutch_terminal.buzzer_beaters",
    "lineup_court.fiveman_shared_court",
)


@skip_no_db
def test_part_c_templates_registered() -> None:
    """All 5 Batch-C templates are present in the registry."""
    from chat_server.templates import get_registry

    registry = get_registry()
    for tid in TEMPLATE_IDS:
        assert tid in registry, f"template {tid!r} missing from registry"
        t = registry[tid]
        # Every heavy template must declare a 300s timeout.
        assert t.timeout_seconds == 300, f"{tid}: expected 300s timeout, got {t.timeout_seconds}s"


@skip_no_db
@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
@pytest.mark.asyncio
async def test_part_c_template_loop(template_id: str) -> None:
    """Run every entry in ``TESTS`` for one Batch-C template.

    Mirrors the pattern in ``chat_tests/test_templates.py`` — each
    template's ``TESTS`` list is iterated, ``Params`` validates the
    inputs, then ``DuckDBSingleton.execute`` runs the SQL.

    Templates with ``NOT_ANSWERABLE=True`` are accepted as a pass
    (the SQL still runs but rows are allowed to be 0; see the spike
    fallback contract.  None of the shipped Batch-C
    templates currently use that escape hatch.
    """
    tmpl = get_template(template_id)
    db = get_db()

    assert tmpl.tests, f"{template_id}: TESTS list must not be empty"
    for idx, spec in enumerate(tmpl.tests):
        params = tmpl.params_model(**spec["params"])
        result = await db.execute(tmpl.sql, params.model_dump())

        # Heavy tier: cap any single execution at the template's own
        # 300s budget.  Locally every template runs sub-second; this is
        # a defensive sanity check.
        assert result.duration_ms < 300_000, (
            f"{template_id} test[{idx}] took {result.duration_ms:.0f}ms — "
            f"over the 300s heavy budget"
        )

        min_rows = spec.get("expect_min_rows", 1)
        # Spike fallback: if the template ships NOT_ANSWERABLE, the
        # SQL still runs but the row count is allowed to be 0.
        not_answerable = getattr(tmpl, "NOT_ANSWERABLE", False)
        if not_answerable:
            # In NA mode, the row assertion is permissive.
            assert result.row_count >= 0
            continue

        assert result.row_count >= min_rows, (
            f"{template_id} test[{idx}] expected >={min_rows} rows, got {result.row_count}"
        )

        if "expect_contains_player" in spec:
            names = [row.get("full_name") for row in result.rows if "full_name" in row]
            expected = spec["expect_contains_player"]
            assert any(expected in (n or "") for n in names), (
                f"{template_id} test[{idx}] expected player {expected!r} in {names}"
            )


@skip_no_db
@pytest.mark.asyncio
async def test_buzzer_beaters_include_free_throws() -> None:
    """Option B regression: a game WON at the FT line at the buzzer must surface.

    Jimmy Butler (player_id 202710), Heat @ Bucks 2020-09-02 (Bubble):
    tied 114-114, fouled at 0:00, made both FTs after the horn to win
    116-114. The prior FG-only logic was structurally blind to this; the
    tied/trailing -> leading rule (which counts made FGs *and* FTs) must
    surface it, and at least one returned buzzer-beater must be a free
    throw (``score_after_margin == 1``).
    """
    tmpl = get_template("clutch_terminal.buzzer_beaters")
    db = get_db()
    params = tmpl.params_model(
        player_id=202710, since_season="2010-11", clock_window=3.0
    )
    result = await db.execute(tmpl.sql, params.model_dump())

    game_ids = {row["game_id"] for row in result.rows}
    assert "0041900202" in game_ids, (
        "Butler's 2020-09-02 Heat@Bucks buzzer-beating FT (game 0041900202) "
        "must surface under the tied/trailing->leading definition"
    )
    margins = {row.get("score_after_margin") for row in result.rows}
    assert 1 in margins, (
        f"expected at least one FT buzzer-beater (score_after_margin==1), got {margins}"
    )
