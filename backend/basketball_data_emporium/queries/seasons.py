"""League/season query helpers for bounded public API surfaces."""

from __future__ import annotations

from typing import Literal

import duckdb

from basketball_data_emporium.db.normalization import season_end_year_sql
from basketball_data_emporium.queries.common import build_rows_response, fetch_dicts
from basketball_data_emporium.server.errors import InvalidSeasonError
from basketball_data_emporium.server.models.catalog import ColumnMeta
from basketball_data_emporium.server.models.common import EndpointRowsResponse
from basketball_data_emporium.server.models.seasons import AvailableSeasonsResponse

LeaderStat = Literal["pts", "reb", "ast", "stl", "blk"]
SeasonType = Literal["Regular", "Playoffs", "Cup"]

_LEADER_RANK_COLUMN: dict[LeaderStat, str] = {
    "pts": "pts_rank",
    "reb": "reb_rank",
    "ast": "ast_rank",
    "stl": "stl_rank",
    "blk": "blk_rank",
}

_STANDINGS_COLUMNS = [
    ColumnMeta(key="season_end_year", label="Season", default_visible=True, numeric=True),
    ColumnMeta(key="team", label="Team", default_visible=True, numeric=False),
    ColumnMeta(key="wins", label="W", default_visible=True, numeric=True),
    ColumnMeta(key="losses", label="L", default_visible=True, numeric=True),
    ColumnMeta(key="win_pct", label="Win%", default_visible=True, numeric=True),
    ColumnMeta(key="net_rating", label="NetRtg", default_visible=True, numeric=True),
    ColumnMeta(key="off_rating", label="ORtg", default_visible=False, numeric=True),
    ColumnMeta(key="def_rating", label="DRtg", default_visible=False, numeric=True),
    ColumnMeta(key="pace", label="Pace", default_visible=False, numeric=True),
    ColumnMeta(key="srs", label="SRS", default_visible=False, numeric=True),
]

_LEADER_COLUMNS = [
    ColumnMeta(key="season_end_year", label="Season", default_visible=True, numeric=True),
    ColumnMeta(key="season_type", label="Type", default_visible=False, numeric=False),
    ColumnMeta(key="full_name", label="Player", default_visible=True, numeric=False),
    ColumnMeta(key="position", label="Pos", default_visible=False, numeric=False),
    ColumnMeta(key="gp", label="GP", default_visible=True, numeric=True),
    ColumnMeta(key="avg_pts", label="PTS", default_visible=True, numeric=True),
    ColumnMeta(key="avg_reb", label="REB", default_visible=True, numeric=True),
    ColumnMeta(key="avg_ast", label="AST", default_visible=True, numeric=True),
    ColumnMeta(key="avg_stl", label="STL", default_visible=False, numeric=True),
    ColumnMeta(key="avg_blk", label="BLK", default_visible=False, numeric=True),
    ColumnMeta(key="fg_pct", label="FG%", default_visible=False, numeric=True),
    ColumnMeta(key="fg3_pct", label="3P%", default_visible=False, numeric=True),
    ColumnMeta(key="ft_pct", label="FT%", default_visible=False, numeric=True),
    ColumnMeta(key="pts_rank", label="PTS Rank", default_visible=False, numeric=True),
    ColumnMeta(key="reb_rank", label="REB Rank", default_visible=False, numeric=True),
    ColumnMeta(key="ast_rank", label="AST Rank", default_visible=False, numeric=True),
    ColumnMeta(key="stl_rank", label="STL Rank", default_visible=False, numeric=True),
    ColumnMeta(key="blk_rank", label="BLK Rank", default_visible=False, numeric=True),
]


def available_seasons(conn: duckdb.DuckDBPyConnection) -> AvailableSeasonsResponse:
    """Return seasons backed by the canonical team-season projection."""
    rows = fetch_dicts(
        conn,
        """
        SELECT DISTINCT SEASON AS season_end_year
        FROM api.v_canonical_team_season
        ORDER BY season_end_year DESC
        """,
    )
    seasons = [int(row["season_end_year"]) for row in rows]
    return AvailableSeasonsResponse(
        seasons=seasons,
        default_season=seasons[0] if seasons else None,
    )


def _assert_season_exists(
    conn: duckdb.DuckDBPyConnection,
    season_end_year: int,
) -> None:
    row = fetch_dicts(
        conn,
        """
        SELECT 1
        FROM api.v_canonical_team_season
        WHERE SEASON = ?
        LIMIT 1
        """,
        [season_end_year],
    )
    if not row:
        raise InvalidSeasonError(
            "Season does not resolve",
            detail={"season_end_year": season_end_year},
        )


def season_standings(
    conn: duckdb.DuckDBPyConnection,
    season_end_year: int,
) -> EndpointRowsResponse:
    """Return one bounded standings table for a season-ending year."""
    _assert_season_exists(conn, season_end_year)
    rows = fetch_dicts(
        conn,
        """
        SELECT
          SEASON AS season_end_year,
          TEAM_ABBR AS team,
          W AS wins,
          L AS losses,
          CASE WHEN W + L > 0 THEN CAST(W AS DOUBLE) / (W + L) END AS win_pct,
          NRtg AS net_rating,
          ORtg AS off_rating,
          DRtg AS def_rating,
          Pace AS pace,
          SRS AS srs
        FROM api.v_canonical_team_season
        WHERE SEASON = ?
        ORDER BY wins DESC, win_pct DESC, team
        LIMIT 40
        """,
        [season_end_year],
    )
    return build_rows_response(
        dataset="standings",
        endpoint_name="season_standings",
        params={"season_end_year": season_end_year},
        columns=_STANDINGS_COLUMNS,
        default_visible_columns=[
            "season_end_year",
            "team",
            "wins",
            "losses",
            "win_pct",
            "net_rating",
        ],
        rows=rows,
    )

def season_leaders(
    conn: duckdb.DuckDBPyConnection,
    season_end_year: int,
    *,
    season_type: SeasonType = "Regular",
    stat: LeaderStat = "pts",
) -> EndpointRowsResponse:
    """Return a bounded season leaders table ordered by the requested stat rank."""
    _assert_season_exists(conn, season_end_year)
    end_expr = season_end_year_sql("season_year")
    rank_column = _LEADER_RANK_COLUMN[stat]
    rows = fetch_dicts(
        conn,
        f"""
        SELECT DISTINCT
          {end_expr} AS season_end_year,
          season_type,
          full_name,
          position,
          gp,
          avg_pts,
          avg_reb,
          avg_ast,
          avg_stl,
          avg_blk,
          fg_pct,
          fg3_pct,
          ft_pct,
          pts_rank,
          reb_rank,
          ast_rank,
          stl_rank,
          blk_rank
        FROM api.v_season_leaders
        WHERE {end_expr} = ?
          AND season_type = ?
          AND {rank_column} IS NOT NULL
        ORDER BY {rank_column}, full_name
        LIMIT 100
        """,
        [season_end_year, season_type],
    )
    if not rows:
        raise InvalidSeasonError(
            "Season leaders do not resolve",
            detail={
                "season_end_year": season_end_year,
                "season_type": season_type,
                "stat": stat,
            },
        )
    return build_rows_response(
        dataset="leaders",
        endpoint_name="season_leaders",
        params={
            "season_end_year": season_end_year,
            "season_type": season_type,
            "stat": stat,
        },
        columns=_LEADER_COLUMNS,
        default_visible_columns=[
            "season_end_year",
            "full_name",
            "gp",
            "avg_pts",
            "avg_reb",
            "avg_ast",
        ],
        rows=rows,
    )
