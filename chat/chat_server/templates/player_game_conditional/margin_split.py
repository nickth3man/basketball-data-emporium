"""``player_game_conditional.margin_split`` template metadata.

PLAN §12 row 6: a player's FG% split between wins and losses decided by
some absolute margin threshold (default 10 points). Uses the player's
``is_win`` flag (carried on every ``fact_player_game_box`` row) joined to
``fact_game_result.margin`` to classify each game.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the margin-split template.

    Attributes
    ----------
    player_id
        ``dim_player.player_id`` to slice on (e.g. ``977`` for Kobe Bryant).
    season_year
        Canonical ``season_year`` like ``'2009-10'``.
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    margin
        Absolute point threshold used to define a "blowout" win or loss.
        Default ``10`` matches PLAN §12 row 6 ("wins by 10+ vs losses by 10+").
    """

    player_id: int = Field(ge=0, description="dim_player.player_id")
    season_year: str = Field(description="Canonical season_year e.g. '2009-10'.")
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")
    margin: int = Field(default=10, ge=1, le=80, description="Absolute margin threshold.")


TEMPLATE_ID = "player_game_conditional.margin_split"
TITLE = "Player FG% in wins-by-N+ vs losses-by-N+"
DESCRIPTION = (
    "A player's per-game FG% split into 'won by at least N points' vs "
    "'lost by at least N points', computed for one season."
)
ALLOWED_TABLES = {"fact_player_game_box", "fact_game_result"}
RESULT_SCHEMA = {
    "split": str,
    "games": int,
    "avg_fg_pct": float,
}
ANSWER_POLICY = "side_by_side_compare"
DEFAULT_LIMIT = 10
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Kobe Bryant FG% in 2009-10 wins by 10+ vs losses by 10+",
    "Compare a player's blowout-win vs blowout-loss FG% for a season",
]
TESTS = [
    {
        "params": {"player_id": 977, "season_year": "2009-10"},
        "expect_min_rows": 2,
        "expect_min_games_per_split": 1,
        "expect_splits": ["losses_by_10p", "wins_by_10p"],
    },
]
