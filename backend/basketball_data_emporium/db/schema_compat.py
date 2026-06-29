"""SQL compatibility projections for warehouse naming drift."""

from __future__ import annotations


DIM_PLAYER_COMPAT_SQL = """
(
    SELECT
      player_id,
      bref_player_id,
      full_name AS display_name,
      full_name,
      is_active,
      json_slug,
      first_name,
      last_name,
      from_year,
      to_year,
      draft_year
    FROM unified_star.dim_player
)
""".strip()


DIM_TEAM_COMPAT_SQL = """
(
    SELECT
      team_id,
      team_abbrev,
      bref_team_code,
      team_city,
      team_name,
      COALESCE(
        NULLIF(TRIM(COALESCE(team_city, '') || ' ' || COALESCE(team_name, '')), ''),
        team_abbrev
      ) AS full_name,
      league,
      season_founded,
      season_active_till
    FROM unified_star.dim_team
)
""".strip()


def dim_player_table(alias: str = "p") -> str:
    """Return the canonical player identity projection with an alias."""
    return f"{DIM_PLAYER_COMPAT_SQL} AS {alias}"


def dim_team_table(alias: str = "t") -> str:
    """Return the canonical team identity projection with an alias."""
    return f"{DIM_TEAM_COMPAT_SQL} AS {alias}"
