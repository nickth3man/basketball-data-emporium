"""``player_game_conditional.milestone_age`` template metadata.

PLAN §12 row 13: the youngest player to record a triple-double (PTS, REB,
AST all >= 10) in a single game, expressed as age in days at game time.
Joins ``fact_player_game_box`` to ``dim_player.birth_date`` and computes
``DATE_DIFF('day', birth_date, game_date)``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the milestone-age template.

    Attributes
    ----------
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    top_n
        How many of the youngest triple-doubles to return. Default 1
        matches the PLAN §12 row 13 benchmark.
    """

    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")
    top_n: int = Field(default=1, ge=1, le=20, description="Rows to return (youngest first).")


TEMPLATE_ID = "player_game_conditional.milestone_age"
TITLE = "Youngest triple-double by age in days"
DESCRIPTION = (
    "Triple-doubles (PTS/REB/AST all >= 10) ranked by the player's age "
    "in days at game time, joining fact_player_game_box to dim_player.birth_date."
)
ALLOWED_TABLES = {"fact_player_game_box", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "game_date": str,
    "age_in_days": int,
    "pts": int,
    "reb": int,
    "ast": int,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 10
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Who is the youngest player to record a triple-double?",
    "Youngest regular-season triple-double by age in days",
]
TESTS = [
    {
        "params": {},
        "expect_min_rows": 1,
        "expect_top_player": "Josh Giddey",
    },
]
