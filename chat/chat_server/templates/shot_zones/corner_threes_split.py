"""``shot_zones.corner_threes_split`` template metadata.

A player's left- vs right-corner 3-point attempts,
makes, and FG% for a given season.

Warehouse adaptation
--------------------
``fact_shot.shot_zone_basic`` was expected to carry
``"Left Corner 3"`` and ``"Right Corner 3"`` values. It does not
(verified at load time — only five distinct values exist: ``"Above the
Break 3"``, ``"Restricted Area"``, ``"Mid-Range"``, ``"In The Paint
(Non-RA)"``, ``"Backcourt"``).

Corner-3 classification is reconstructed via ``shot_zone_basic = 'Above
the Break 3'`` plus an ``|loc_x| >= 220`` threshold (NBA Stats API
convention). The CASE expression yields canonical corner-3 labels so the
composer can phrase the comparison naturally.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the corner-threes-split template.

    Attributes
    ----------
    player_id
        ``dim_player.player_id`` of the shooter.
    season_year
        Canonical ``"YYYY-YY"`` form (e.g. ``"2016-17"``).
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    """

    player_id: int = Field(ge=1, description="dim_player.player_id of the shooter.")
    season_year: str = Field(description="Canonical season_year, e.g. '2016-17'.")
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")


TEMPLATE_ID = "shot_zones.corner_threes_split"
TITLE = "Left- vs right-corner 3-point shooting for a player in a season"
DESCRIPTION = (
    "Attempts, makes, and FG% for the left- vs right-corner 3-point "
    "zones (NBA Stats API definition: shot_zone_basic = 'Above the "
    "Break 3' AND |loc_x| >= 220) for a given player + season."
)
ALLOWED_TABLES = {"fact_shot"}
RESULT_SCHEMA = {
    "shot_zone": str,
    "attempts": int,
    "makes": int,
    "pct": float,
}
ANSWER_POLICY = "comparison"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Steph Curry's left- vs right-corner threes in 2016-17",
    "Compare a player's corner-3 splits for a single season",
]
TESTS = [
    {
        "params": {"player_id": 201939, "season_year": "2016-17", "season_type": "Regular"},
        "expect_min_rows": 2,
        "expect_contains_player": None,
    },
    {
        "params": {"player_id": 201939, "season_year": "2014-15", "season_type": "Regular"},
        "expect_min_rows": 2,
    },
]
