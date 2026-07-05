"""``player_game_conditional.career_conditional_aggregate`` template metadata.

PLAN §12 row 19: players with the most career assists in games where
they scored zero points. Aggregates over ``fact_player_game_box`` with
``pts = 0``, summed per player, joined to ``dim_player`` for the name.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the career-conditional-aggregate template.

    Attributes
    ----------
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    top_n
        Number of rows to return. Default ``5``.
    """

    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")
    top_n: int = Field(default=5, ge=1, le=100, description="Rows to return.")


TEMPLATE_ID = "player_game_conditional.career_conditional_aggregate"
TITLE = "Most career assists in games where the player scored 0"
DESCRIPTION = (
    "Career totals of assists accumulated in games where the player "
    "recorded zero points, summed from fact_player_game_box."
)
ALLOWED_TABLES = {"fact_player_game_box", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "games_scored_zero": int,
    "total_ast": int,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 60
EXAMPLES = [
    "Which player has the most career assists in scoreless games?",
    "Career assists when scoring zero points",
]
TESTS = [
    {
        "params": {},
        "expect_min_rows": 1,
        "expect_contains_player": "Steve Blake",
    },
]
