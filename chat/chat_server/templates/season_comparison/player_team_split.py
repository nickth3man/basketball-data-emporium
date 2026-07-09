"""``season_comparison.player_team_split`` template metadata.

Harden 2022-23 PHI-vs-BKN per-game + team win% after the
mid-season trade. **Not answerable with evidence in this warehouse.**

The trade event is not captured canonically. ``mart_player_season``
exposes only the post-trade PHI rows for Harden in 2022-23 (a single
team_id partition). A "team split" requires a transaction-aware grain
that this warehouse does not have.

This template's SQL is intentionally an *evidence query*: it returns the
rows that do exist (the PHI rows), so the composer can attach them as
evidence when emitting the not-answerable response. The module-level
constants ``NOT_ANSWERABLE = True`` and ``NOT_ANSWERABLE_NOTE`` are the
new optional fields a future composer / agent honors to switch into the
transparent not-answerable-with-evidence path.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the (not-answerable) player-team-split template.

    Attributes
    ----------
    player_id
        ``dim_player.player_id`` to inspect (e.g. ``201935`` for Harden).
    season_year
        Canonical ``season_year`` like ``'2022-23'``.
    season_type
        ``"Regular"`` (default). The "evidence" rows are filtered by it.
    """

    player_id: int = Field(ge=0, description="dim_player.player_id")
    season_year: str = Field(description="Canonical season_year.")
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")


TEMPLATE_ID = "season_comparison.player_team_split"
TITLE = "Player per-game split across teams in a season (NOT ANSWERABLE)"
DESCRIPTION = (
    "Per-team splits of a player's season averages (e.g. PHI vs BKN "
    "after a mid-season trade). NOT ANSWERABLE: the warehouse's "
    "mart_player_season only carries one team partition per player-season, "
    "so the trade event is invisible."
)
ALLOWED_TABLES = {"mart_player_season", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "team_id": int,
    "team_abbreviation": str,
    "season_year": str,
    "season_type": str,
    "gp": int,
    "avg_pts": float,
}
ANSWER_POLICY = "not_answerable"
DEFAULT_LIMIT = 20
TIMEOUT_SECONDS = 30
NOT_ANSWERABLE = True
NOT_ANSWERABLE_NOTE = (
    "The mid-season trade event isn't captured canonically; "
    "mart_player_season exposes only one team's rows for the player in "
    "this season (PHI for Harden 2022-23). A per-team split requires a "
    "transaction-aware grain the warehouse doesn't have."
)
EXAMPLES = [
    "Harden 2022-23 PHI vs BKN per-game after the trade",
    "Player team splits in a season",
]
TESTS = [
    {
        "params": {"player_id": 201935, "season_year": "2022-23"},
        "expect_min_rows": 1,
        "expect_only_team_abbr": "PHI",
    },
]
