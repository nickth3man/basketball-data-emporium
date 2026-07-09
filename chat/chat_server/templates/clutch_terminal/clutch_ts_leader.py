"""``clutch_terminal.clutch_ts_leader`` template metadata.

Highest True Shooting percentage in a single postseason's
clutch window.  The canonical ``agg_clutch_stats`` mart is
an ``empty_endpoint_shell``, so we derive clutch TS% live from
``fact_pbp_event``.

Clutch window
-------------
* ``period >= 4`` (Q4 and any OT periods).
* ``seconds_elapsed >= period_end - clutch_minutes * 60`` where
  ``period_end = 2880`` for Q4 and ``2880 + (period - 4) * 300`` for OT
  (OT periods are 5 min; Q4 is 12 min).
* ``|score_home - score_away| <= clutch_margin`` at the time of the event.

True Shooting formula
---------------------
TS% = (FGM*shot_value + FTM) / (2 * (FGA + 0.44 * FTA))

The result set is bounded by ``min_attempts`` (default 5 FGA) to suppress
single-shot noise and ordered by ``clutch_ts_pct DESC``.  The 2023
playoffs are tagged ``season_year='2022-23'`` (NBA convention — the
postseason belongs to the season that started the previous fall).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the clutch-TS% leader template.

    Attributes
    ----------
    season_year
        Single-season filter (e.g. ``'2022-23'`` for the 2023 playoffs).
    season_type
        ``"Playoffs"`` (default), ``"Regular"``, or ``"Cup"``.
    clutch_minutes
        Width of the clutch window in minutes (default 5).
    clutch_margin
        Max |score_home - score_away| at event time (default 5).
    min_attempts
        Minimum FGA to qualify (default 5).
    top_n
        Maximum number of leaders to return.
    """

    season_year: str = Field(description="Single season filter, e.g. '2022-23'.")
    season_type: str = Field(default="Playoffs", description="Regular | Playoffs | Cup")
    clutch_minutes: int = Field(default=5, ge=1, le=12, description="Clutch window in minutes.")
    clutch_margin: int = Field(default=5, ge=0, le=50, description="Max score margin.")
    min_attempts: int = Field(default=5, ge=1, le=100, description="Min FGA to qualify.")
    top_n: int = Field(default=10, ge=1, le=50, description="Number of leaders to return.")


TEMPLATE_ID = "clutch_terminal.clutch_ts_leader"
TITLE = "Highest TS% in the postseason clutch window"
DESCRIPTION = (
    "For a single postseason, ranks players by True Shooting percentage "
    "in the clutch window (last $clutch_minutes minutes of Q4 + OT, "
    "score within $clutch_margin).  Filters to players with at least "
    "$min_attempts FGA in that window."
)
ALLOWED_TABLES = {"fact_pbp_event", "dim_game", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "clutch_pts": int,
    "clutch_fga": int,
    "clutch_ftm": int,
    "clutch_fta": int,
    "clutch_ts_pct": float,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 300
EXAMPLES = [
    "Highest TS% in 2023 playoff clutch",
    "Clutch TS% leaders in the 2022-23 NBA playoffs",
    "Best playoff clutch shooter by TS% in 2022-23 (last 5 min, within 5 pts)",
]
TESTS = [
    {
        "params": {
            "season_year": "2022-23",
            "season_type": "Playoffs",
            "clutch_minutes": 5,
            "clutch_margin": 5,
            "min_attempts": 5,
            "top_n": 10,
        },
        "expect_min_rows": 1,
    },
]
