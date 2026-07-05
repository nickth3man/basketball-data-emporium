"""``season_comparison.league_pace_era`` template metadata.

PLAN §12 row 15: compare average league pace across two seasons. Uses
``fact_player_game_advanced.pace`` (a player-game proxy for team pace,
since this warehouse does not expose a true team-game pace table).

Caveat (documented for the answer composer): this is a player-game
average, not a team-game league aggregate. A small bias is possible
(starters and rotation players carry the most weight), but the
season-over-season delta is reliable because the bias is roughly stable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the league-pace-era template.

    Attributes
    ----------
    season_a, season_b
        Canonical ``season_year`` strings like ``'1998-99'`` and
        ``'2022-23'``. The result has one row per season.
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    """

    season_a: str = Field(description="Canonical season_year (e.g. '1998-99').")
    season_b: str = Field(description="Canonical season_year (e.g. '2022-23').")
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")


TEMPLATE_ID = "season_comparison.league_pace_era"
TITLE = "League average pace, two-season era comparison"
DESCRIPTION = (
    "Average pace per 100 possessions, season vs season, computed as a "
    "player-game average over fact_player_game_advanced."
)
ALLOWED_TABLES = {"fact_player_game_advanced"}
RESULT_SCHEMA = {
    "season_year": str,
    "avg_pace": float,
    "sample_games": int,
}
ANSWER_POLICY = "side_by_side_compare"
DEFAULT_LIMIT = 10
TIMEOUT_SECONDS = 60
EXAMPLES = [
    "League average pace in 1998-99 vs 2022-23",
    "How much has league pace changed between two seasons?",
]
TESTS = [
    {
        "params": {"season_a": "1998-99", "season_b": "2022-23"},
        "expect_min_rows": 2,
        "expect_seasons": ["1998-99", "2022-23"],
        "expect_avg_pace_strictly_increasing": True,
    },
]
