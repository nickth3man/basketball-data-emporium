"""``season_thresholds.rookie_vs_final`` template metadata.

PLAN §12 row 16: a single player's points-per-game and rebounds-per-game
in their rookie season and final season of the given ``season_type``.

Implementation notes
--------------------
The canonical ``mart_player_season`` mart exposes per-game ``avg_pts`` and
``avg_reb`` (verified at load time — see chat_tests/fixtures/), so no
source-backed fallback is required. The rookie / final seasons are
identified by ``ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY
season_year)`` in ascending and descending directions respectively.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the rookie-vs-final template.

    Attributes
    ----------
    player_id
        ``dim_player.player_id`` of the target player. The agent resolves
        free-text names to ids via its ``lookup_player`` tool before
        invoking the template; the dev CLI accepts either form via the
        template's documented helper (see ``scripts/run_template.py``).
    season_type
        ``"Regular"`` (default), ``"Playoffs"``, or ``"Cup"``.
    """

    player_id: int = Field(ge=1, description="dim_player.player_id of the target player.")
    season_type: str = Field(default="Regular", description="Regular | Playoffs | Cup")


TEMPLATE_ID = "season_thresholds.rookie_vs_final"
TITLE = "Rookie vs final-season PPG / RPG for a single player"
DESCRIPTION = (
    "Returns a player's rookie-season and final-season points-per-game "
    "and rebounds-per-game (for the requested season_type), side by side. "
    "Rookie = earliest season_year for that player; final = latest."
)
ALLOWED_TABLES = {"mart_player_season", "dim_player"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "season_year": str,
    "avg_pts": float,
    "avg_reb": float,
    "row_kind": str,
}
ANSWER_POLICY = "comparison"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Tim Duncan rookie vs final season points and rebounds per game",
    "Compare LeBron James's first and last regular-season scoring and rebounding averages",
]
TESTS = [
    {
        # Tim Duncan: player_id=1495, rookie 1997-98, final 2015-16.
        "params": {"player_id": 1495, "season_type": "Regular"},
        "expect_min_rows": 2,
        "expect_contains_player": "Tim Duncan",
    },
    {
        # Sanity check: an active legend (LeBron) — still playing, so
        # rookie/final may coincide if the test is rerun later. Pin both
        # edges explicitly to keep the test stable for now.
        "params": {"player_id": 2544, "season_type": "Regular"},
        "expect_min_rows": 2,
        "expect_contains_player": "LeBron James",
    },
]
