"""``career_demographic.hs_draftee_career_ws`` template metadata.

PLAN §12 row 1: top-N career win shares among high-school-drafted players,
with each draftee's drafting team.

Source-backed
-------------
Win shares are not exposed by any canonical mart; this template
allowlists ``src_agg_player_season_advanced`` (the verified warehouse
table — see chat_tests/fixtures/) per the source-backed extension
described in PLAN §3.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Params(BaseModel):
    """Parameters for the HS-draftee career-WS template.

    Attributes
    ----------
    top_n
        How many HS draftees to return, ordered by career WS DESC.
        Default 3 matches the PLAN §12 row-1 benchmark.
    """

    top_n: int = Field(default=3, ge=1, le=50, description="Number of HS draftees to return.")


TEMPLATE_ID = "career_demographic.hs_draftee_career_ws"
TITLE = "Top N career win shares among high-school-drafted players"
DESCRIPTION = (
    "High-school-drafted players (fact_draft.organization_type = 'High School') "
    "ranked by career regular-season win shares (src_agg_player_season_advanced.ws), "
    "with each player's drafting team."
)
ALLOWED_TABLES = {"fact_draft", "dim_player", "src_agg_player_season_advanced"}
RESULT_SCHEMA = {
    "player_id": int,
    "full_name": str,
    "drafting_team": str,
    "career_ws": float,
}
ANSWER_POLICY = "ranked_list"
DEFAULT_LIMIT = 50
TIMEOUT_SECONDS = 30
EXAMPLES = [
    "Top 3 career win shares among HS-drafted players",
    "Which high school draftees had the most career win shares?",
]
TESTS = [
    {
        # PLAN §12 row 1: LeBron James is the all-time HS-draftee WS leader.
        "params": {"top_n": 3},
        "expect_min_rows": 3,
        "expect_contains_player": "LeBron James",
    },
    {
        # Larger fan-out still includes the leader at #1.
        "params": {"top_n": 10},
        "expect_min_rows": 10,
        "expect_contains_player": "LeBron James",
    },
]
