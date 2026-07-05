"""Integration tests for the Phase-6 Batch-A templates.

Mirrors ``test_templates.py``'s pattern: each template ships a ``TESTS``
list in its metadata module and this module runs every spec against the
live warehouse via the dev ``DuckDBSingleton``.

Hard-coded to the Batch-A templates (PLAN §12 rows 1, 3, 9, 16, 20):

* ``season_thresholds.rookie_vs_final``           (row 16)
* ``career_demographic.hs_draftee_career_ws``     (row  1)
* ``career_demographic.country_gp_leaders``       (row 20)
* ``teammate_overlap.two_player_shared_team_seasons`` (row  3)
* ``shot_zones.corner_threes_split``              (row  9)

Skipped cleanly when ``data/nba.duckdb`` is absent (see conftest.skip_no_db).
"""

from __future__ import annotations

import pytest

from chat_server.db import get_db
from chat_server.templates import get_template
from chat_tests.conftest import skip_no_db

#: Batch-A templates this fixer owns. Sibling batches own the other
#: template families; expanding this list would couple unrelated changes.
BATCH_A_TEMPLATE_IDS: tuple[str, ...] = (
    "season_thresholds.rookie_vs_final",
    "career_demographic.hs_draftee_career_ws",
    "career_demographic.country_gp_leaders",
    "teammate_overlap.two_player_shared_team_seasons",
    "shot_zones.corner_threes_split",
)


@skip_no_db
@pytest.mark.parametrize("template_id", BATCH_A_TEMPLATE_IDS)
async def test_batch_a_template_loop(template_id: str) -> None:
    """Run every entry in ``tmpl.tests`` against the live warehouse.

    A parametrised test per template keeps pytest's output targeted: if
    one template regresses, only its row shows the failure. Each entry's
    ``params`` is validated through the template's Pydantic model before
    being bound to the SQL.
    """
    tmpl = get_template(template_id)
    db = get_db()

    for idx, spec in enumerate(tmpl.tests):
        params = tmpl.params_model(**spec["params"])
        result = await db.execute(tmpl.sql, params.model_dump())

        min_rows = spec.get("expect_min_rows", 1)
        assert result.row_count >= min_rows, (
            f"{template_id} test[{idx}] expected >= {min_rows} rows, got {result.row_count}"
        )

        # ``expect_contains_player`` is only meaningful when the result
        # schema includes a player-name column. Most templates expose it
        # as ``full_name``; country_gp_leaders names its top scorer via
        # ``top_scorer_full_name``. A spec may override the lookup with
        # ``expect_player_column``; default to ``full_name``.
        if "expect_contains_player" in spec and spec["expect_contains_player"]:
            player_col = spec.get("expect_player_column", "full_name")
            names = [row[player_col] for row in result.rows]
            expected = spec["expect_contains_player"]
            assert expected in names, (
                f"{template_id} test[{idx}] expected {expected!r} in column {player_col!r}: {names}"
            )


@skip_no_db
def test_batch_a_templates_registered() -> None:
    """Every Batch-A template id is present in the registry."""
    from chat_server.templates import get_registry

    registry = get_registry()
    for template_id in BATCH_A_TEMPLATE_IDS:
        assert template_id in registry, (
            f"expected {template_id!r} in registry; got {sorted(registry)}"
        )
