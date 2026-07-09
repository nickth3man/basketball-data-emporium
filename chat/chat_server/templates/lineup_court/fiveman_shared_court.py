"""``lineup_court.fiveman_shared_court`` template metadata.

Aggregate stats for a 5-man lineup that shared the
court in a single season.

Spike outcome
-------------
**Shipped as a REAL template.**  Phase 6 verification showed:

* ``fact_lineup_player`` (canonical lineup-roster map) has per-game
  rows per player-in-lineup, keyed by a
  ``team_id-game_id-season_year`` ``group_id``.
* ``src_agg_lineup_efficiency`` (lineup-stats source table) holds
  per-game totals — ``total_gp``, ``total_min``, ``avg_net_rating``,
  ``total_plus_minus`` — keyed by the same ``group_id``.
* For the 2017-18 Warriors lineup (Curry / Thompson / Iguodala /
  Durant / Draymond Green), 5 group_ids match with combined ~861 min.

The plan's source-backed table ``src_fact_lineup_stats`` is empty in
the current warehouse; ``src_agg_lineup_efficiency`` is the working
replacement and is allowlisted explicitly.  The spike fallback
(``NOT_ANSWERABLE=True``) was NOT taken because the possession-stitch
cost does not materialise — the aggregate
columns are precomputed in ``src_agg_lineup_efficiency``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the 5-man shared-court template.

    Attributes
    ----------
    player_ids
        The 5 ``dim_player.player_id`` values that must appear together
        in a lineup unit.  Length must equal ``player_count`` (default 5).
    season_year
        Single season filter, e.g. ``'2017-18'``.
    player_count
        Exact size of the lineup unit (default 5).  Pinned at 5 today
        but kept parameterised for future variants (e.g. 3-man, 4-man
        units).
    """

    player_ids: list[int] = Field(
        min_length=1,
        max_length=10,
        description=(
            "Player IDs that must co-occur in a lineup unit. "
            "Default: 2017-18 Warriors 5-man unit (Curry / Thompson / "
            "Iguodala / Durant / Green)."
        ),
    )
    season_year: str = Field(description="Single season filter, e.g. '2017-18'.")
    player_count: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Exact size of the lineup unit (default 5).",
    )


TEMPLATE_ID = "lineup_court.fiveman_shared_court"
TITLE = "5-man shared-court lineup aggregate stats"
DESCRIPTION = (
    "Returns every game where the requested N players co-occur in the "
    "exact-N lineup unit, with per-game total_min / avg_net_rating / "
    "total_plus_minus / total_gp from src_agg_lineup_efficiency."
)
ALLOWED_TABLES = {"fact_lineup_player", "src_agg_lineup_efficiency"}
RESULT_SCHEMA = {
    "group_id": str,
    "total_gp": int,
    "total_min": float,
    "avg_net_rating": float,
    "total_plus_minus": float,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 300
EXAMPLES = [
    "2017-18 Warriors 5-man lineup Curry Thompson Iguodala Durant Green net rating and minutes",
    "How many minutes did the Death Lineup play in 2017-18",
    "Net rating of the 2017-18 Warriors starting 5",
]
TESTS = [
    {
        "params": {
            "player_ids": [201939, 202691, 2738, 201142, 203110],
            "season_year": "2017-18",
        },
        "expect_min_rows": 1,
    },
]
