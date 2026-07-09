"""``clutch_terminal.buzzer_beaters`` template metadata.

Game-winning buzzer-beaters for a single player + their
opponents in each game.

Spike outcome
-------------
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
clock-reliability concern does not materialise
on the live warehouse for the supported ``since_season`` lower bound.

Definition recap (see SQL header for the algorithm)
---------------------------------------------------
Aligned with Basketball-Reference's canonical rule ("successful shots
taken with the shooter's team tied or trailing which left no time on the
clock"; BBR's list explicitly counts free throws). The buzzer-beater is
the made scoring play (field goal OR free throw) that produced the game's
FINAL lead change:

1. Play is a made FG or made FT (``shot_result='Made'``, ``shot_value``
   1/2/3), in the last ``$clock_window`` seconds of Q4 or OT.
2. Immediately before the play the scoring team was tied or trailing
   (``margin_before <= 0``); immediately after it the team leads
   (``margin_after > 0``) -- i.e. the play flipped the lead.
3. It is that game's LAST such lead flip by the eventual winner
   (``rn_last_flip = 1``), so it is the play that put the winner ahead
   for good.
4. The team that scored won the game (``team_id = winner_team_id``).

This supersedes an earlier FG-only definition (last made FG by the
winner) that was both blind to free throws (so games won at the FT line
at the buzzer -- e.g. Jimmy Butler, Heat @ Bucks 2020-09-02 Bubble -- were
missed) and had no tied/trailing condition (so insurance FGs scored while
already leading qualified). Kobe Bryant's verified count drops from 17 to
14 under this definition; the 3 removed were last-3s FGs with the Lakers
already up 3-7.
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
