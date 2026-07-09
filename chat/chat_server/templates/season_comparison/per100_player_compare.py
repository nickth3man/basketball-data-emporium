"""``season_comparison.per100_player_compare`` template metadata.

Head-to-head per-100 scoring between two named players in
a single season. The warehouse's canonical ``mart_player_season`` does not
expose per-100 metrics, so this template is source-backed against
``src_stg_bref_per_100_poss`` (BBR's per-100 player-season table).

Verification note: ``fact_player_game_advanced.poss`` is only populated
for the 2025-26 season in this warehouse build, so per-100 cannot be
computed on the fly from the canonical advanced box for historical
seasons. The BBR per-100 source is the canonical substitute.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the per-100 compare template.

    Attributes
    ----------
    player_a_id, player_b_id
        Two ``dim_player.player_id`` values to compare.
    season_year
        Canonical ``season_year`` like ``'2021-22'``; the SQL translates
        this to ``season_end_year = CAST(SUBSTR(season_year,1,4) AS INT)+1``.
    """

    player_a_id: int = Field(ge=0, description="dim_player.player_id (player A).")
    player_b_id: int = Field(ge=0, description="dim_player.player_id (player B).")
    season_year: str = Field(description="Canonical season_year e.g. '2021-22'.")


TEMPLATE_ID = "season_comparison.per100_player_compare"
TITLE = "Two players' per-100 scoring in a single season"
DESCRIPTION = (
    "Side-by-side per-100-possession scoring (and assist) totals for two "
    "named players in one season, sourced from BBR's per_100_poss table."
)
ALLOWED_TABLES = {"src_stg_bref_per_100_poss", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "per_100_pts": float,
    "per_100_ast": float,
    "games": int,
}
ANSWER_POLICY = "side_by_side_compare"
DEFAULT_LIMIT = 10
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Trae Young vs Luka Doncic points per 100 in 2021-22",
    "Per-100 scoring comparison between two players in a season",
]
TESTS = [
    {
        "params": {
            "player_a_id": 1629027,
            "player_b_id": 1629029,
            "season_year": "2021-22",
        },
        "expect_min_rows": 2,
        "expect_per_100_pts_positive": True,
        "expect_contains_player": "Trae Young",
    },
]
