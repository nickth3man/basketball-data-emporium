"""``season_thresholds.fifty_forty_ninety`` template metadata.

50-40-90 (FG% >= 50, 3P% >= 40, FT% >= 90) seasons
filtered by a minimum PPG floor. Phase 1 default uses Regular season.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the 50-40-90 template.

    Attributes
    ----------
    min_ppg
        Minimum points per game for inclusion. Default 25.0 matches the
        canonical benchmark question.
    fg_min
        Minimum FG% (default 0.50).
    fg3_min
        Minimum 3P% (default 0.40).
    ft_min
        Minimum FT% (default 0.90).
    season_type
        One of ``"Regular"``, ``"Playoffs"``, ``"Cup"`` (matches the
        ``mart_player_season.season_type`` vocabulary).
    """

    min_ppg: float = Field(default=25.0, ge=0, le=100, description="Minimum points per game.")
    fg_min: float = Field(default=0.50, ge=0, le=1, description="Minimum FG%.")
    fg3_min: float = Field(default=0.40, ge=0, le=1, description="Minimum 3P%.")
    ft_min: float = Field(default=0.90, ge=0, le=1, description="Minimum FT%.")
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")


TEMPLATE_ID = "season_thresholds.fifty_forty_ninety"
TITLE = "50-40-90 seasons with minimum PPG"
DESCRIPTION = (
    "Players who shot >=50% FG, >=40% 3P, >=90% FT in a season at a given scoring threshold."
)
ALLOWED_TABLES = {"mart_player_season", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "season_year": str,
    "fg_pct": float,
    "fg3_pct": float,
    "ft_pct": float,
    "avg_pts": float,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "50-40-90 seasons with at least 25 PPG",
    "Who shot 50/40/90 and averaged 25+ points?",
]
TESTS = [
    {
        "params": {"min_ppg": 25.0},
        "expect_min_rows": 1,
        "expect_contains_player": "Stephen Curry",
    },
]
