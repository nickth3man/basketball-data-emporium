"""Team search, summary, dataset, and export queries."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import duckdb

from basketball_data_emporium.db.csv_export import stream_csv_response
from basketball_data_emporium.db.registry import require_dataset_binding
from basketball_data_emporium.db.schema_compat import dim_player_table, dim_team_table
from basketball_data_emporium.queries.common import (
    build_rows_response,
    fetch_dicts,
    fetch_one,
    season_end_expr,
    team_dataset_meta,
)
from basketball_data_emporium.server.errors import InvalidSeasonError, InvalidTeamError
from basketball_data_emporium.server.models.common import EndpointRowsResponse
from basketball_data_emporium.server.models.teams import (
    FeaturedTeam,
    FeaturedTeamsResponse,
    FranchiseArcPoint,
    TeamHeroStats,
    TeamHubSummary,
    TeamSearchResult,
)

FEATURED_TEAMS: tuple[tuple[str, str | None], ...] = (
    ("LAL", "Tied for most NBA titles"),
    ("BOS", "Tied for most NBA titles"),
    ("GSW", None),
    ("CHI", None),
    ("SAN", None),
)


def validate_featured_teams(conn: duckdb.DuckDBPyConnection) -> None:
    """Fail fast if a curated featured team abbreviation no longer resolves."""
    missing: list[str] = []
    for identifier, _blurb in FEATURED_TEAMS:
        row = fetch_one(
            conn,
            f"""
            SELECT 1
            FROM {dim_team_table("t")}
            WHERE UPPER(team_abbrev) = UPPER(?) OR UPPER(bref_team_code) = UPPER(?)
            LIMIT 1
            """,
            [identifier, identifier],
        )
        if row is None:
            missing.append(identifier)
    if missing:
        raise InvalidTeamError(
            "Featured team identifier does not resolve",
            detail={"identifiers": missing},
        )


def _team_or_404(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    *,
    season_end_year: int | None = None,
) -> dict[str, Any]:
    season_filter = ""
    params: list[Any] = [identifier, identifier]
    if season_end_year is not None:
        season_filter = """
          AND COALESCE(season_founded, 0) <= ?
          AND COALESCE(season_active_till, 9999) >= ?
        """
        params.extend([season_end_year, season_end_year])
    row = fetch_one(
        conn,
        f"""
        SELECT team_id, team_abbrev, full_name, league, season_founded, season_active_till
        FROM {dim_team_table("t")}
        WHERE (UPPER(team_abbrev) = UPPER(?) OR UPPER(bref_team_code) = UPPER(?))
        {season_filter}
        ORDER BY
          CASE WHEN league = 'NBA' THEN 0 ELSE 1 END,
          season_active_till DESC,
          season_founded DESC
        LIMIT 1
        """,
        params,
    )
    if row is None:
        raise InvalidTeamError(
            "Team identifier does not resolve",
            detail={"identifier": identifier},
        )
    return row


def search_teams(conn: duckdb.DuckDBPyConnection, term: str) -> list[TeamSearchResult]:
    pattern = f"%{term.strip()}%"
    rows = fetch_dicts(
        conn,
        f"""
        SELECT team_abbrev AS identifier, full_name, league
        FROM {dim_team_table("t")}
        WHERE team_abbrev ILIKE ? OR full_name ILIKE ?
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY team_abbrev
          ORDER BY CASE WHEN league = 'NBA' THEN 0 ELSE 1 END, season_active_till DESC
        ) = 1
        ORDER BY
          CASE WHEN UPPER(team_abbrev) = UPPER(?) THEN 0
               WHEN LOWER(full_name) = LOWER(?) THEN 1
               WHEN full_name ILIKE ? THEN 2
               ELSE 3 END,
          CASE WHEN COALESCE(season_active_till, 9999) >= 2026 THEN 0 ELSE 1 END,
          team_abbrev,
          full_name
        LIMIT 20
        """,
        [pattern, pattern, term.strip(), term.strip(), f"{term.strip()}%"],
    )
    return [
        TeamSearchResult(name=row["full_name"], identifier=row["identifier"], leagues=[row["league"]])
        for row in rows
    ]


def featured_teams(conn: duckdb.DuckDBPyConnection) -> FeaturedTeamsResponse:
    teams: list[FeaturedTeam] = []
    for identifier, blurb in FEATURED_TEAMS:
        row = _team_or_404(conn, identifier)
        teams.append(
            FeaturedTeam(
                name=row["full_name"],
                identifier=row["team_abbrev"],
                blurb=blurb,
                leagues=[row["league"]],
            )
        )
    return FeaturedTeamsResponse(teams=teams)


def _roster_rows(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    *,
    season_end_year: int | None = None,
) -> list[dict[str, Any]]:
    team = _team_or_404(conn, identifier, season_end_year=season_end_year)
    end_expr = season_end_expr("s.season_year")
    if season_end_year is None:
        season_row = fetch_one(
            conn,
            f"""
            SELECT MAX({end_expr}) AS season_end_year
            FROM unified_star.fact_player_season_stats s
            WHERE s.team_id = ? AND s.is_playoffs = false
            """,
            [team["team_id"]],
        )
        season_end_year = int(season_row["season_end_year"]) if season_row else None
    if season_end_year is None:
        return []

    rows = fetch_dicts(
        conn,
        f"""
        SELECT
          p.display_name AS full_name,
          p.bref_player_id AS bref_player_id,
          {end_expr} AS season_end_year,
          s.gp AS gp,
          s.min AS mp,
          s.per AS per
        FROM unified_star.fact_player_season_stats s
        JOIN {dim_player_table("p")} ON p.player_id = s.player_id
        WHERE s.team_id = ?
          AND s.is_playoffs = false
          AND {end_expr} = ?
        ORDER BY s.min DESC NULLS LAST, p.display_name
        LIMIT 500
        """,
        [team["team_id"], season_end_year],
    )
    if not rows:
        raise InvalidSeasonError(
            "Season does not resolve for team roster",
            detail={"identifier": identifier, "season_end_year": season_end_year},
        )
    return rows


def _franchise_arc_rows(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
) -> list[dict[str, Any]]:
    team = _team_or_404(conn, identifier)
    end_expr = season_end_expr("season_year")
    return fetch_dicts(
        conn,
        f"""
        SELECT
          {end_expr} AS season_end_year,
          w AS wins,
          l AS losses,
          CASE WHEN w + l > 0 THEN CAST(w AS DOUBLE) / (w + l) END AS win_pct
        FROM unified_star.fact_team_season_summary
        WHERE team_id = ?
        ORDER BY season_end_year
        LIMIT 500
        """,
        [team["team_id"]],
    )


def team_dataset(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    dataset: str,
    *,
    season_end_year: int | None = None,
    include_inactive_games: bool = False,  # noqa: ARG001 - reserved for future datasets
) -> EndpointRowsResponse:
    endpoint_name, columns, default_visible = team_dataset_meta(dataset)
    require_dataset_binding("team", dataset)
    if dataset == "roster":
        rows = _roster_rows(conn, identifier, season_end_year=season_end_year)
    elif dataset == "franchise-arc":
        rows = _franchise_arc_rows(conn, identifier)
    else:
        rows = []
    return build_rows_response(
        dataset=dataset,
        endpoint_name=endpoint_name,
        params={
            "identifier": identifier,
            "season_end_year": season_end_year,
            "include_inactive_games": include_inactive_games,
        },
        columns=columns,
        default_visible_columns=default_visible,
        rows=rows,
    )


def team_summary(conn: duckdb.DuckDBPyConnection, identifier: str) -> TeamHubSummary:
    team = _team_or_404(conn, identifier)
    end_expr = season_end_expr("season_year")
    seasons = fetch_dicts(
        conn,
        f"""
        SELECT DISTINCT {end_expr} AS season_end_year
        FROM unified_star.fact_team_season_summary
        WHERE team_id = ?
        ORDER BY season_end_year DESC
        """,
        [team["team_id"]],
    )
    available = [int(row["season_end_year"]) for row in seasons]
    default = available[0] if available else None
    hero_row = None
    if default is not None:
        hero_row = fetch_one(
            conn,
            f"""
            SELECT
              {end_expr} AS season,
              w AS wins,
              l AS losses,
              CASE WHEN w + l > 0 THEN CAST(w AS DOUBLE) / (w + l) END AS win_pct,
              o_rtg AS off_rtg,
              d_rtg AS def_rtg
            FROM unified_star.fact_team_season_summary
            WHERE team_id = ? AND {end_expr} = ?
            LIMIT 1
            """,
            [team["team_id"], default],
        )
    arc_rows = _franchise_arc_rows(conn, identifier)
    return TeamHubSummary(
        identifier=team["team_abbrev"],
        display_name=team["full_name"],
        leagues=[team["league"]],
        default_season=default,
        available_seasons=available,
        hero_stats=TeamHeroStats(team=team["team_abbrev"], **(hero_row or {})),
        roster=team_dataset(conn, identifier, "roster", season_end_year=default),
        franchise_arc=[
            FranchiseArcPoint(team_name=team["full_name"], **row)
            for row in arc_rows
        ],
    )


def team_csv(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    dataset: str,
    *,
    season_end_year: int | None = None,
    include_inactive_games: bool = False,
) -> Iterator[bytes]:
    response = team_dataset(
        conn,
        identifier,
        dataset,
        season_end_year=season_end_year,
        include_inactive_games=include_inactive_games,
    )
    fieldnames = [column.key for column in response.columns]
    return stream_csv_response(fieldnames, response.rows)
