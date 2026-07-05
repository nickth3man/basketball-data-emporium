"""``player_game_conditional.rare_stat_line`` template metadata.

PLAN §12 row 17: regular-season quadruple-doubles since 1954 (PTS, REB,
AST, BLK all >= 10). The warehouse confirms two qualifying games for the
canonical "10 in four categories" definition (Nate Thurmond 1974-10-18
and David Robinson 1994-02-17). Alvin Robertson 1986-02-18 also
qualifies but his quad-double swaps BLK for STL; the template sticks to
the canonical 4-category PTS/REB/AST/BLK definition and uses a single
``min_stat`` threshold so callers can probe other stat lines (e.g.
5x5, 6x6) with the same shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the rare-stat-line template.

    Attributes
    ----------
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    min_stat
        Minimum value for each of PTS/REB/AST/BLK. Default ``10`` matches
        the canonical "quadruple-double" definition in PLAN §12 row 17.
    """

    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")
    min_stat: int = Field(default=10, ge=1, le=80, description="Threshold for each category.")


TEMPLATE_ID = "player_game_conditional.rare_stat_line"
TITLE = "Quadruple-doubles (or any N-category stat line) since 1954"
DESCRIPTION = (
    "Regular-season games where a player recorded PTS, REB, AST, and BLK "
    "all >= min_stat. Defaults to the canonical quadruple-double "
    "(min_stat=10)."
)
ALLOWED_TABLES = {"fact_player_game_box", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "game_date": str,
    "pts": int,
    "reb": int,
    "ast": int,
    "blk": int,
    "season_year": str,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Regular-season quadruple-doubles since 1954",
    "Games where a player had at least 10 PTS, 10 REB, 10 AST, 10 BLK",
]
TESTS = [
    {
        "params": {},
        "expect_min_rows": 1,
        "expect_contains_player": "Nate Thurmond",
    },
]
