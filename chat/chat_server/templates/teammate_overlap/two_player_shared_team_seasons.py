"""``teammate_overlap.two_player_shared_team_seasons`` template metadata.

PLAN §12 row 3: every other player who shared at least one (team,
season_year) with both of two named players (regular season by default).

Derivation
----------
``bridge_player_team_season`` does not exist in the warehouse (verified at
load time). Team-season membership is derived from
``fact_player_game_box`` (DISTINCT (team_id, season_year) per player).
NULL ``team_id`` rows (All-Star games / league-wide events) are excluded
to avoid a phantom shared "team" of opposing All-Stars.

Answer policy
-------------
The benchmark question asks specifically for *active* players. The
template returns the ``is_active`` flag from ``dim_player``; the
composer/UI applies the active filter at presentation time. The SQL
itself intentionally returns all shared-team-season players so the
filter is a single source of truth in the answer layer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the two-player shared-team-seasons template.

    Attributes
    ----------
    player_a_id
        ``dim_player.player_id`` of player A.
    player_b_id
        ``dim_player.player_id`` of player B.
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    """

    player_a_id: int = Field(ge=1, description="dim_player.player_id of player A.")
    player_b_id: int = Field(ge=1, description="dim_player.player_id of player B.")
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")


TEMPLATE_ID = "teammate_overlap.two_player_shared_team_seasons"
TITLE = "Players who shared a team-season with both of two named players"
DESCRIPTION = (
    "Returns every other player who appeared in `fact_player_game_box` "
    "for at least one (team_id, season_year) that both `player_a_id` and "
    "`player_b_id` played for in the given `season_type`. Useful for "
    "'who is the intersection of teammates' style questions."
)
ALLOWED_TABLES = {"fact_player_game_box", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "is_active": bool,
    "team_id": int,
    "season_year": str,
}
ANSWER_POLICY = "list"
DEFAULT_LIMIT = 200
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Players who played with both LeBron James and Dwyane Wade in the regular season",
    "Which active players were teammates of two specific stars?",
]
TESTS = [
    {
        # Benchmark deviation: PLAN §12 row 3 names LeBron + CP3, but those
        # two never shared a regular-season team in the warehouse (verified
        # via DISTINCT (team_id, season_year) INTERSECT — only the All-Star
        # ghost row matches). We test with LeBron + Wade instead, who
        # shared MIA 2010-14 + CLE 2017-18.
        "params": {"player_a_id": 2544, "player_b_id": 2548, "season_type": "Regular"},
        "expect_min_rows": 1,
        "expect_contains_player": "Chris Bosh",
    },
    {
        # Document the empty-result case explicitly: PLAN's canonical pair.
        "params": {"player_a_id": 2544, "player_b_id": 101108, "season_type": "Regular"},
        "expect_min_rows": 0,
    },
]
