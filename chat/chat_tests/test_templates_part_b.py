"""Phase 6 BATCH B integration tests.

Live-warehouse parametrized assertions for the 9 templates shipped in
TEMPLATE_BATCH_B. Each
test runs the template's own ``TESTS`` list against the warehouse and
checks the per-spec assertions. The suite mirrors the Phase 1
``test_fifty_forty_ninety_loop`` shape but generalized via the
``_run_template_tests`` helper.

Fixtures (in ``chat_tests/fixtures/``) are referenced only by ID — these
tests run the SQL directly and assert the expected rows / counts /
contains, not the on-disk JSON. The JSON fixtures exist for downstream
tools / composer examples and remain stable (``status: stable``).

Skip behavior: every test uses ``@skip_no_db`` (defined in
``chat_tests/conftest.py``) so the suite is a no-op when the warehouse
file is absent.
"""

from __future__ import annotations

from typing import Any

import pytest

from chat_server.db import get_db
from chat_server.templates import get_template
from chat_tests.conftest import skip_no_db

# The 9 template ids owned by this batch. Loop tests parametrize over
# this list rather than the registry so a missing template is a clear
# test failure instead of a silent skip.
TEMPLATE_IDS_BATCH_B = (
    "player_game_conditional.margin_split",
    "player_game_conditional.milestone_age",
    "player_game_conditional.rare_stat_line",
    "player_game_conditional.streak_stat",
    "player_game_conditional.career_conditional_aggregate",
    "season_comparison.per100_player_compare",
    "season_comparison.league_pace_era",
    "season_comparison.player_team_split",
    "team_coach.franchise_final_season_ortg",
)


def _assert_template_test(
    template_id: str,
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
    row_count: int,
) -> None:
    """Apply the spec's assertions against the rows returned by the live warehouse.

    Centralizes the asserts so each per-template test stays focused on
    its own loop body. Supported spec keys:
      * expect_min_rows (int)             — fail if row_count < N
      * expect_min_games_per_split (int)  — every split's `games` column >= N
      * expect_splits (list[str])         — every label present in the split column
      * expect_top_player (str)           — first row's `full_name` equals this
      * expect_contains_player (str)      — at least one row's `full_name` matches
      * expect_seasons (list[str])        — every season_year present
      * expect_avg_pace_strictly_increasing (bool) — pace values strictly increase
      * expect_per_100_pts_positive (bool)         — every row's per_100_pts > 0
      * expect_contains_coach (str)       — at least one row's coach_name matches
      * expect_team_off_rating_positive (bool) — at least one row's team_off_rating > 0
      * expect_only_team_abbr (str)       — every row's team_abbreviation == this
      * expect_not_answerable (bool)      — module exposes NOT_ANSWERABLE=True
    """
    if "expect_min_rows" in spec:
        assert row_count >= spec["expect_min_rows"], (
            f"{template_id}: expected >={spec['expect_min_rows']} rows, got {row_count}"
        )

    if "expect_splits" in spec:
        split_values = {row["split"] for row in rows}
        for expected_split in spec["expect_splits"]:
            assert expected_split in split_values, (
                f"{template_id}: expected split {expected_split!r} in {split_values}"
            )

    if "expect_min_games_per_split" in spec:
        threshold = spec["expect_min_games_per_split"]
        for row in rows:
            assert row["games"] >= threshold, (
                f"{template_id}: split {row['split']!r} has only {row['games']} games"
            )

    if "expect_top_player" in spec:
        assert rows, f"{template_id}: expected non-empty result for top player check"
        assert rows[0]["full_name"] == spec["expect_top_player"], (
            f"{template_id}: expected top player {spec['expect_top_player']!r}, "
            f"got {rows[0]['full_name']!r}"
        )

    if "expect_contains_player" in spec:
        names = [row["full_name"] for row in rows]
        assert spec["expect_contains_player"] in names, (
            f"{template_id}: expected {spec['expect_contains_player']!r} in {names}"
        )

    if "expect_seasons" in spec:
        got = {row["season_year"] for row in rows}
        for season in spec["expect_seasons"]:
            assert season in got, f"{template_id}: expected {season!r} in {got}"

    if spec.get("expect_avg_pace_strictly_increasing"):
        paces = [row["avg_pace"] for row in rows]
        assert paces == sorted(paces) and len(set(paces)) == len(paces), (
            f"{template_id}: expected strictly increasing paces, got {paces}"
        )

    if spec.get("expect_per_100_pts_positive"):
        for row in rows:
            assert row["per_100_pts"] > 0, (
                f"{template_id}: row per_100_pts={row['per_100_pts']} not positive: {row}"
            )

    if "expect_contains_coach" in spec:
        coaches = [row["coach_name"] for row in rows]
        assert spec["expect_contains_coach"] in coaches, (
            f"{template_id}: expected {spec['expect_contains_coach']!r} in {coaches}"
        )

    if spec.get("expect_team_off_rating_positive"):
        ortgs = [row["team_off_rating"] for row in rows]
        assert any(o > 0 for o in ortgs), f"{template_id}: no positive team_off_rating: {ortgs}"

    if "expect_only_team_abbr" in spec:
        abbrs = {row["team_abbreviation"] for row in rows}
        assert abbrs == {spec["expect_only_team_abbr"]}, (
            f"{template_id}: expected only team_abbreviation "
            f"{spec['expect_only_team_abbr']!r}, got {abbrs}"
        )

    if spec.get("expect_not_answerable"):
        tmpl = get_template(template_id)
        assert getattr(tmpl.params_model.__class__, "__module__", None), (
            f"{template_id}: missing params_model"
        )
        module = __import__(tmpl.params_model.__module__, fromlist=["NOT_ANSWERABLE"])
        assert getattr(module, "NOT_ANSWERABLE", False) is True, (
            f"{template_id}: expected module NOT_ANSWERABLE=True for not-answerable spec"
        )


async def _run_template_tests(template_id: str) -> None:
    """Iterate every ``TESTS`` entry for a template against the warehouse."""
    tmpl = get_template(template_id)
    db = get_db()
    assert tmpl.tests, f"{template_id} has no TESTS list"

    for idx, spec in enumerate(tmpl.tests):
        params = tmpl.params_model(**spec["params"])
        result = await db.execute(tmpl.sql, params.model_dump())
        _assert_template_test(
            template_id,
            spec,
            list(result.rows),
            result.row_count,
        )
        # Quiet the unused-arg lint from the test runner's per-spec loop.
        _ = idx


@skip_no_db
@pytest.mark.parametrize("template_id", TEMPLATE_IDS_BATCH_B)
async def test_batch_b_template(template_id: str) -> None:
    """Run every ``TESTS`` entry for one template; skip when no warehouse."""
    await _run_template_tests(template_id)


@skip_no_db
def test_batch_b_templates_present() -> None:
    """All 9 batch-B templates are registered in the live registry."""
    from chat_server.templates import get_registry

    registry = get_registry()
    missing = [tid for tid in TEMPLATE_IDS_BATCH_B if tid not in registry]
    assert not missing, f"missing batch-B templates: {missing}"


@skip_no_db
def test_player_team_split_is_marked_not_answerable() -> None:
    """The not-answerable template (#4) advertises its evidence-only contract."""
    import importlib

    from chat_server.templates import get_template

    get_template("season_comparison.player_team_split")
    mod = importlib.import_module("chat_server.templates.season_comparison.player_team_split")
    assert getattr(mod, "NOT_ANSWERABLE", False) is True
    assert getattr(mod, "NOT_ANSWERABLE_NOTE", None), "missing NOT_ANSWERABLE_NOTE"
    assert mod.ANSWER_POLICY == "not_answerable"
