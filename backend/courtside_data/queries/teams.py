"""Team search, summary, dataset, and export queries."""

from __future__ import annotations

import csv
import io
from typing import Any

import duckdb

from courtside_data.queries.common import (
    build_rows_response,
    csv_escape_value,
    fetch_dicts,
    fetch_one,
    season_end_expr,
    team_dataset_meta,
)
from courtside_data.server.errors import InvalidSeasonError, InvalidTeamError
from courtside_data.server.models.common import EndpointRowsResponse
from courtside_data.server.models.teams import (
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
    ("SAS", None),
)

# TODO P1-BE-05: validate `FEATURED_TEAMS` at startup. The featured sidebar is
# curated, so stale abbreviations should fail fast before the API starts
# accepting traffic.


def _team_name(row: dict[str, Any]) -> str:
    return f"{row['team_city']} {row['team_name']}".strip()


def _team_or_404(conn: duckdb.DuckDBPyConnection, identifier: str) -> dict[str, Any]:
    # TODO P1-BE-04: this resolver chooses one modern-ish team row by abbrev or
    # BBR code. Historical queries need a season-aware resolver that returns the
    # correct `dim_team` row for a requested season and filters by
    # `season_founded <= season_end_year <= season_active_till`.
    row = fetch_one(
        conn,
        """
        SELECT team_id, team_abbrev, team_city, team_name, league
        FROM unified_star.dim_team
        WHERE UPPER(team_abbrev) = UPPER(?) OR UPPER(bref_team_code) = UPPER(?)
        ORDER BY
          CASE WHEN league = 'NBA' THEN 0 ELSE 1 END,
          season_active_till DESC,
          season_founded DESC
        LIMIT 1
        """,
        [identifier, identifier],
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
        """
        SELECT team_abbrev AS identifier, team_city, team_name, league
        FROM unified_star.dim_team
        WHERE team_abbrev ILIKE ? OR team_city ILIKE ? OR team_name ILIKE ?
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY team_abbrev
          ORDER BY CASE WHEN league = 'NBA' THEN 0 ELSE 1 END, season_active_till DESC
        ) = 1
        ORDER BY
          CASE WHEN UPPER(team_abbrev) = UPPER(?) THEN 0
               WHEN team_city ILIKE ? OR team_name ILIKE ? THEN 1
               ELSE 2 END,
          team_city,
          team_name
        LIMIT 20
        """,
        [pattern, pattern, pattern, term.strip(), f"{term.strip()}%", f"{term.strip()}%"],
    )
    return [
        TeamSearchResult(name=_team_name(row), identifier=row["identifier"], leagues=[row["league"]])
        for row in rows
    ]


def featured_teams(conn: duckdb.DuckDBPyConnection) -> FeaturedTeamsResponse:
    teams: list[FeaturedTeam] = []
    for identifier, blurb in FEATURED_TEAMS:
        row = _team_or_404(conn, identifier)
        teams.append(
            FeaturedTeam(
                name=_team_name(row),
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
    # TODO P2-BE-03: `include_inactive_games` is not meaningful for roster
    # rows. If the UI keeps a global "include inactive games" toggle, registry
    # metadata should declare whether each dataset supports it so unsupported
    # controls can be hidden or disabled.
    team = _team_or_404(conn, identifier)
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
          p.full_name AS full_name,
          p.bref_player_id AS bref_player_id,
          {end_expr} AS season_end_year,
          s.gp AS gp,
          s.min AS mp,
          s.per AS per
        FROM unified_star.fact_player_season_stats s
        JOIN unified_star.dim_player p ON p.player_id = s.player_id
        WHERE s.team_id = ?
          AND s.is_playoffs = false
          AND {end_expr} = ?
        ORDER BY s.min DESC NULLS LAST, p.full_name
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


def team_dataset(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    dataset: str,
    *,
    season_end_year: int | None = None,
    include_inactive_games: bool = False,  # noqa: ARG001 - reserved for future datasets
) -> EndpointRowsResponse:
    # TODO P1-BE-02: route this through `db/registry.py`. The team side
    # currently has only `roster`, but franchise history, standings, team game
    # logs, four factors, and lineups should all be declarative registry
    # bindings with shared filtering/pagination.
    #
    # TODO P1-BE-03: validate season ranges centrally before calling roster
    # SQL. Empty-result `invalid_season` is not precise enough for bad input
    # such as 1800 or a future season outside the DB snapshot.
    endpoint_name, columns, default_visible = team_dataset_meta(dataset)
    rows = _roster_rows(conn, identifier, season_end_year=season_end_year)
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
    # TODO P2-BE-02: graduate `franchise_arc` into an explicit team dataset
    # once registry-backed franchise history lands. Summary can keep a compact
    # preview, but the tab should expose the full table with columns/exports.
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
    arc_rows = fetch_dicts(
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
        """,
        [team["team_id"]],
    )
    return TeamHubSummary(
        identifier=team["team_abbrev"],
        display_name=_team_name(team),
        leagues=[team["league"]],
        default_season=default,
        available_seasons=available,
        hero_stats=TeamHeroStats(team=team["team_abbrev"], **(hero_row or {})),
        roster=team_dataset(conn, identifier, "roster", season_end_year=default),
        franchise_arc=[
            FranchiseArcPoint(team_name=_team_name(team), **row)
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
) -> str:
    # TODO P2-BE-04: move CSV generation to the shared streaming export module.
    # Roster is small today; future team game logs and lineup datasets need a
    # chunked response with the same formula-injection guard.
    response = team_dataset(
        conn,
        identifier,
        dataset,
        season_end_year=season_end_year,
        include_inactive_games=include_inactive_games,
    )
    output = io.StringIO()
    fieldnames = [column.key for column in response.columns]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in response.rows:
        writer.writerow({key: csv_escape_value(row.get(key)) for key in fieldnames})
    return output.getvalue()
