"""``career_demographic.country_gp_leaders`` template metadata.

PLAN §12 row 20: among non-USA countries, which has the most players with
at least ``min_gp`` career games, and who is the highest career-points
scorer from each qualifying country?

Composition
-----------
Templates emit a single result set (one row per qualifying country, with
that country's top career-points scorer attached) so the answer composer
can summarize all countries in one pass.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the country GP leaders template.

    Attributes
    ----------
    min_gp
        Minimum career games played to count as a "leader".
        Default 500 matches the PLAN §12 row-20 benchmark.
    top_n
        How many top countries to return, ranked by player count DESC.
    """

    min_gp: int = Field(default=500, ge=1, description="Minimum career GP to qualify.")
    top_n: int = Field(default=5, ge=1, le=50, description="Number of top countries to return.")


TEMPLATE_ID = "career_demographic.country_gp_leaders"
TITLE = "Non-USA countries with the most career-long players"
DESCRIPTION = (
    "Among non-USA countries, ranks them by the count of players with at "
    "least `min_gp` career games and attaches each country's all-time "
    "highest career-points scorer."
)
ALLOWED_TABLES = {"dim_player", "mart_player_career"}
RESULT_SCHEMA = {
    "country": str,
    "player_count": int,
    "top_scorer_full_name": str,
    "top_scorer_career_pts": int,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Which non-USA country has the most 500-game-career players?",
    "Countries with the most career-long NBA players and their top scorer",
]
TESTS = [
    {
        # Default min_gp (500) — at least one non-USA country qualifies
        # (verified: Canada leads with 13 players; Steve Nash top scorer).
        "params": {"min_gp": 500, "top_n": 5},
        "expect_min_rows": 1,
        "expect_contains_player": "Steve Nash",
        "expect_player_column": "top_scorer_full_name",
    },
    {
        # Lower min_gp threshold — broader fan-out (verified: 10 qualifying
        # countries when min_gp=300).
        "params": {"min_gp": 300, "top_n": 10},
        "expect_min_rows": 3,
        "expect_contains_player": "Dirk Nowitzki",
        "expect_player_column": "top_scorer_full_name",
    },
]
