"""``pbp_aggregate.largest_scoring_run`` template metadata.

The largest scoring run in any NBA Finals game since
2010.  Built on top of ``fact_pbp_event`` (scoring-event islands) and
``dim_game`` (Finals filter).  Heavy tier — 300s timeout.

Why it's "heavy"
----------------
The query reads every scoring play in every Finals game in the window
and runs a gaps-and-islands window function over them.  The
``dim_game.game_label='NBA Finals'`` pre-filter keeps the working set
small (~100 games), but the window functions are O(N log N) per game.
A full-fact scan is not feasible, hence the ``season_year`` lower
bound — see the SQL header for the detailed algorithm.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the largest-scoring-run template.

    Attributes
    ----------
    since_season
        Lower bound (inclusive) on ``dim_game.season_year``.  Default
        ``'2009-10'`` matches the canonical benchmark (since the 2010 Finals).
    top_n
        Maximum number of runs to return, ordered by ``run_points DESC``.
    """

    since_season: str = Field(
        default="2009-10",
        description="Inclusive lower bound on dim_game.season_year (e.g. '2009-10').",
    )
    top_n: int = Field(default=5, ge=1, le=50, description="Number of runs to return.")


TEMPLATE_ID = "pbp_aggregate.largest_scoring_run"
TITLE = "Largest scoring run in an NBA Finals game"
DESCRIPTION = (
    "For every NBA Finals game since $since_season, identify the "
    "largest scoring run (consecutive made field goals / free throws "
    "by one team without the other team scoring).  Returns the top-N "
    "runs by total points scored during the run."
)
ALLOWED_TABLES = {"fact_pbp_event", "dim_game"}
RESULT_SCHEMA = {
    "game_id": str,
    "season_year": str,
    "game_date": str,
    "scoring_team_id": int,
    "run_points": int,
    "scoring_plays": int,
    "run_start_period": int,
    "run_start_elapsed": float,
    "run_end_elapsed": float,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 300
EXAMPLES = [
    "Largest scoring run in an NBA Finals game since 2010",
    "Top 5 scoring runs in the NBA Finals",
    "Biggest scoring run by a single team in a Finals game since 2009-10",
]
TESTS = [
    {
        "params": {"since_season": "2009-10", "top_n": 5},
        "expect_min_rows": 1,
    },
]
