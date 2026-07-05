"""``clutch_terminal.buzzer_beaters`` template metadata.

PLAN §12 row 18: game-winning buzzer-beaters for a single player + their
opponents in each game.

Spike outcome (PLAN §15)
------------------------
**Shipped as a REAL template.**  Phase 6 verification against
``data/nba.duckdb`` showed:

* The PBP ``clock`` column is reliably populated down to hundredths of
  a second across all seasons since 1996-97 (Kobe's first season).
* For ``player_id=977`` (Kobe Bryant), ``since_season='1996-97'``,
  ``clock_window=3.0`` returns 17 verified buzzer-beater candidates
  (including the famous 2009-12-04 jumper over Dwyane Wade and the
  2000-05-10 WCF Game-7 jumper vs Portland).  See
  ``chat_tests/test_templates_part_c.py`` and the spike fixture
  ``chat_tests/fixtures/clutch_terminal__buzzer_beaters_kobe_1996_97.json``.

Spike fallback (``NOT_ANSWERABLE=True``) was NOT taken because the
clock-reliability concern PLAN §12 row 18 flagged does not materialise
on the live warehouse for the supported ``since_season`` lower bound.

Definition recap (see SQL header for the algorithm)
---------------------------------------------------
1. Shot is a made field goal (Pascal-case + lowercase both supported).
2. Shot is in the last ``$clock_window`` seconds of Q4 or OT (clock is
   parsed as ISO 8601 ``PT{MM}M{SS}.{HH}S``).
3. Shot is the LAST made field goal of the entire game.
4. The team that shot won the game (``pbp.team_id = dim_game.winner_team_id``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the buzzer-beaters template.

    Attributes
    ----------
    player_id
        ``dim_player.player_id`` of the target player.
    since_season
        Inclusive lower bound on ``dim_game.season_year`` (default
        ``'1996-97'`` — Kobe's rookie year).
    clock_window
        Maximum remaining-clock seconds for the shot to qualify
        (default 3.0 — last 3 seconds).
    """

    player_id: int = Field(ge=1, description="dim_player.player_id of the target player.")
    since_season: str = Field(
        default="1996-97",
        description="Inclusive lower bound on dim_game.season_year.",
    )
    clock_window: float = Field(
        default=3.0,
        ge=0.0,
        le=10.0,
        description="Maximum remaining-clock seconds (default 3.0).",
    )


TEMPLATE_ID = "clutch_terminal.buzzer_beaters"
TITLE = "Game-winning buzzer-beaters for a player"
DESCRIPTION = (
    "Lists every made field goal in the last $clock_window seconds of "
    "Q4 or OT that won the game (shot by the winning team in its final "
    "made-FG play of the game), along with the opponent team_id and "
    "final score."
)
ALLOWED_TABLES = {"fact_pbp_event", "dim_game"}
RESULT_SCHEMA = {
    "game_id": str,
    "season_year": str,
    "game_date": str,
    "period": int,
    "clock": str,
    "scoring_team_id": int,
    "opponent_team_id": int,
    "home_score": int,
    "away_score": int,
    "score_after_margin": int,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 300
EXAMPLES = [
    "Kobe Bryant game-winning buzzer-beaters and opponents",
    "List all buzzer-beaters Kobe made in his career",
    "Michael Jordan game-winning buzzer-beaters since 1984-85",
]
TESTS = [
    {
        "params": {"player_id": 977, "since_season": "1996-97", "clock_window": 3.0},
        "expect_min_rows": 1,
    },
]
