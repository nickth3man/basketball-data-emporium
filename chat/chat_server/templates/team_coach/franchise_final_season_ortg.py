"""``team_coach.franchise_final_season_ortg`` template metadata.

PLAN §12 row 2: a franchise's final season — head coach + team offensive
rating. The "franchise final season" is derived from
``dim_team_era.valid_to_year`` (the last non-current era for that
``team_id``), with an opt-out via the ``final_season`` parameter for
explicit callers.

Team offensive rating is computed as the average of per-player-game
``off_rating`` from ``fact_player_game_advanced`` over all Regular-season
player-games for the team+season.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the franchise-final-season-ortg template.

    Attributes
    ----------
    team_id
        ``dim_team.team_id`` (e.g. ``1610612760`` for Seattle/OKC).
    final_season
        Optional override for the derived final season. When ``None`` the
        SQL computes ``(valid_to_year - 1) || '-' || (valid_to_year % 100)``
        from ``dim_team_era``.
    """

    team_id: int = Field(ge=0, description="dim_team.team_id (the franchise).")
    final_season: str | None = Field(
        default=None, description="Override the derived final season; None = auto."
    )


TEMPLATE_ID = "team_coach.franchise_final_season_ortg"
TITLE = "Franchise final-season head coach + team offensive rating"
DESCRIPTION = (
    "Returns the head coach and team offensive rating for a franchise's "
    "final season, derived from dim_team_era when final_season is None."
)
ALLOWED_TABLES = {
    "fact_coach_season",
    "fact_player_game_advanced",
    "dim_team_era",
}
RESULT_SCHEMA = {
    "coach_name": str,
    "season_year": str,
    "team_off_rating": float,
    "team_abbreviation": str,
}
ANSWER_POLICY = "single_fact"
DEFAULT_LIMIT = 10
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Seattle SuperSonics final season head coach and team offensive rating",
    "Head coach + team ORtg for a franchise's last season",
]
TESTS = [
    {
        "params": {"team_id": 1610612760, "final_season": "2007-08"},
        "expect_min_rows": 1,
        "expect_contains_coach": "P.J. Carlesimo",
        "expect_team_off_rating_positive": True,
    },
]
