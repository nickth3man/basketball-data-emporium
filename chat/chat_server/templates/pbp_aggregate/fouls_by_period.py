"""``pbp_aggregate.fouls_by_period`` template metadata.

Most offensive fouls committed in a given period
(default 4th quarter) of a single season.

Foul taxonomy
-------------
The warehouse ``fact_pbp_event`` has ~99K
``Foul|Offensive`` rows and ~15K ``Foul|Offensive Charge`` rows.  These
are the two ``action_type='Foul'`` sub-types we treat as "offensive
fouls committed".  ``Turnover|Foul`` rows (~102K) are excluded — those
are *take-fouls* (deliberate fouls to stop the clock), not offensive
fouls.

Case sensitivity note
---------------------
The PBP source alternates between Pascal-case (``Foul``/``Offensive``)
and lowercase (``foul``/``offensive``) across seasons.  The template
uses ``LOWER(action_type)`` / ``LOWER(sub_type)`` so both casings are
captured.  Verified during Phase 6: 2022-23 only has lowercase rows,
so the lowercase predicate is the load-bearing one for the benchmark
question.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the offensive-fouls-by-period template.

    Attributes
    ----------
    season_year
        The single season to filter on (e.g. ``'2022-23'``).
    period
        The period (1–10).  Default 4 matches the canonical benchmark.
    top_n
        Maximum number of players to return.
    """

    season_year: str = Field(description="Single season filter, e.g. '2022-23'.")
    period: int = Field(default=4, ge=1, le=10, description="Period number (1-10).")
    top_n: int = Field(default=10, ge=1, le=100, description="Number of players to return.")


TEMPLATE_ID = "pbp_aggregate.fouls_by_period"
TITLE = "Most offensive fouls in a period"
DESCRIPTION = (
    "For a given season and period, ranks players by the number of "
    "offensive fouls committed (action_type='Foul' AND sub_type IN "
    "('Offensive','Offensive Charge'), case-insensitive).  Excludes "
    "Turnover|Foul (take-fouls)."
)
ALLOWED_TABLES = {"fact_pbp_event", "dim_game", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "offensive_fouls": int,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 300
EXAMPLES = [
    "Most offensive fouls in 4th quarters in 2022-23",
    "Players who committed the most offensive fouls in Q4 of 2022-23",
    "Offensive foul leaders in period 4, 2022-23 NBA season",
]
TESTS = [
    {
        "params": {"season_year": "2022-23", "period": 4, "top_n": 10},
        "expect_min_rows": 1,
    },
]
