"""``player_game_conditional.streak_stat`` template metadata.

PLAN §12 row 14: longest run of consecutive games (in calendar order)
where the player recorded at least one blocked shot. Uses a classic
gaps-and-islands pattern: rank games per player by date, then group by
``game_date - rank`` so consecutive-day games share the same group key.

Limitation (documented for callers): ``fact_player_game_box`` only
contains rows for games the player actually appeared in, so a streak
spans only consecutive games the player played (with BLK >= 1).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the streak-stat template.

    Attributes
    ----------
    player_id
        Optional ``dim_player.player_id`` to filter on. ``None`` (default)
        returns the longest streak(s) across every player.
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    min_streak_games
        Discard runs shorter than this many games. Default ``2`` keeps
        trivial single-game streaks out of the result.
    """

    player_id: int | None = Field(
        default=None, ge=0, description="dim_player.player_id; None = all."
    )
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")
    min_streak_games: int = Field(default=2, ge=1, le=100, description="Discard shorter runs.")


TEMPLATE_ID = "player_game_conditional.streak_stat"
TITLE = "Longest run of consecutive games with a blocked shot"
DESCRIPTION = (
    "Gaps-and-islands aggregation over fact_player_game_box: finds the "
    "longest run of consecutive calendar games per player where BLK >= 1."
)
ALLOWED_TABLES = {"fact_player_game_box", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "streak_games": int,
    "streak_start": str,
    "streak_end": str,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 60
EXAMPLES = [
    "Most consecutive games with a blocked shot",
    "Longest BLK streak for Dikembe Mutombo",
]
TESTS = [
    {
        "params": {},
        "expect_min_rows": 1,
    },
    {
        "params": {"min_streak_games": 5},
        "expect_min_rows": 1,
        "expect_contains_player": "Kareem Abdul-Jabbar",
    },
]
