"""Player search, summary, dataset, and export queries."""

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
    player_dataset_meta,
)
from courtside_data.server.errors import InvalidPlayerError, InvalidSeasonError
from courtside_data.server.models.common import EndpointRowsResponse
from courtside_data.server.models.players import (
    FeaturedAthlete,
    FeaturedAthletesResponse,
    PlayerHubSummary,
    PlayerSearchResult,
)

FEATURED_PLAYERS: tuple[tuple[str, str | None], ...] = (
    ("jamesle01", "All-time scoring leader"),
    ("jordami01", None),
    ("curryst01", None),
    ("birdla01", None),
)

# TODO P1-BE-05: validate `FEATURED_PLAYERS` once at startup instead of inside
# each featured request. A stale curated slug should produce a startup
# diagnostic or health failure, not a per-request 404 path.


def _player_or_404(conn: duckdb.DuckDBPyConnection, identifier: str) -> dict[str, Any]:
    player = fetch_one(
        conn,
        """
        SELECT player_id, bref_player_id, full_name
        FROM unified_star.dim_player
        WHERE bref_player_id = ?
        """,
        [identifier],
    )
    if player is None:
        raise InvalidPlayerError(
            "Player identifier does not resolve",
            detail={"identifier": identifier},
        )
    return player


def search_players(conn: duckdb.DuckDBPyConnection, term: str) -> list[PlayerSearchResult]:
    pattern = f"%{term.strip()}%"
    rows = fetch_dicts(
        conn,
        """
        SELECT bref_player_id AS identifier, full_name AS name
        FROM unified_star.dim_player
        WHERE bref_player_id ILIKE ? OR full_name ILIKE ?
        ORDER BY
          CASE WHEN bref_player_id = ? THEN 0
               WHEN full_name ILIKE ? THEN 1
               ELSE 2 END,
          full_name
        LIMIT 20
        """,
        [pattern, pattern, term.strip(), f"{term.strip()}%"],
    )
    return [
        PlayerSearchResult(name=row["name"], identifier=row["identifier"], leagues=["NBA"])
        for row in rows
    ]


def featured_players(conn: duckdb.DuckDBPyConnection) -> FeaturedAthletesResponse:
    athletes: list[FeaturedAthlete] = []
    for identifier, blurb in FEATURED_PLAYERS:
        row = _player_or_404(conn, identifier)
        athletes.append(
            FeaturedAthlete(
                name=row["full_name"],
                identifier=row["bref_player_id"],
                blurb=blurb,
                leagues=["NBA"],
            )
        )
    return FeaturedAthletesResponse(athletes=athletes)


def _career_rows(conn: duckdb.DuckDBPyConnection, identifier: str) -> list[dict[str, Any]]:
    # TODO P1-BE-08: this query is a per-season career arc, not career totals.
    # Either move it behind a `season-totals`/`career-arc` dataset ID or add a
    # separate aggregate row for the catalog's "Career Totals" promise.
    #
    # TODO P2-DB-02: derived fields (`points_per_game`,
    # `total_rebounds_per_game`, `assists_per_game`) need declared formula
    # lineage in the manifest/registry so the UI-consumed keys are tested like
    # physical DB columns.
    return fetch_dicts(
        conn,
        """
        SELECT
          SEASON AS season_end_year,
          CAST(SEASON - 1 AS VARCHAR) || '-' || LPAD(CAST(SEASON % 100 AS VARCHAR), 2, '0') AS season,
          TEAM_ABBR AS team,
          LEAGUE AS league,
          G AS gp,
          MP AS mp,
          PTS AS pts,
          TRB AS reb,
          AST AS ast,
          STL AS stl,
          BLK AS blk,
          TOV AS tov,
          CASE WHEN G > 0 THEN PTS / G END AS points_per_game,
          CASE WHEN G > 0 THEN TRB / G END AS total_rebounds_per_game,
          CASE WHEN G > 0 THEN AST / G END AS assists_per_game
        FROM api.v_canonical_player_season_totals
        WHERE PLAYER_ID = ?
        ORDER BY SEASON DESC, TEAM_ABBR
        LIMIT 500
        """,
        [identifier],
    )


def player_dataset(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    dataset: str,
    *,
    season_end_year: int | None = None,
    include_inactive_games: bool = False,  # noqa: ARG001 - reserved for game-log datasets
) -> EndpointRowsResponse:
    # TODO P1-BE-02: dispatch through `db/registry.py` instead of branching on
    # dataset ID here. The registry should own source object, projection list,
    # filters, default order, export support, and schema-drift expectations.
    #
    # TODO P1-BE-03: validate `season_end_year` centrally before querying. This
    # function currently accepts any integer and only raises `invalid_season`
    # when the filtered query returns no rows.
    #
    # TODO P1-BE-07: apply a server-side timeout/cancellation policy. Frontend
    # request timeouts do not stop DuckDB scans, so each dataset needs a bounded
    # execution policy before larger views are exposed.
    #
    # TODO P2-BE-03: either implement `include_inactive_games` for game-log
    # datasets or hide the toggle where the selected dataset cannot honor it.
    #
    # TODO P2-BE-05: replace the fixed `LIMIT 500` pattern with explicit
    # pagination, stable sorting, and truthful row-count semantics.
    _player_or_404(conn, identifier)
    endpoint_name, columns, default_visible = player_dataset_meta(dataset)

    if dataset == "career":
        rows = _career_rows(conn, identifier)
    elif dataset == "adjusted-shooting":
        # TODO P1-BE-04: this join uses `team_id` only. When team dimensions
        # have multiple historical rows for the same ID, apply a shared
        # season-active join helper so player/team labels are not duplicated or
        # mismatched.
        params: list[Any] = [identifier]
        season_filter = ""
        if season_end_year is not None:
            season_filter = "AND CAST(s.season_year AS VARCHAR) IN (?, ?)"
            params.extend([str(season_end_year), f"{season_end_year - 1}-{season_end_year % 100:02d}"])
        rows = fetch_dicts(
            conn,
            f"""
            SELECT
              CASE WHEN CAST(s.season_year AS VARCHAR) LIKE '%-%'
                   THEN CAST(SUBSTR(CAST(s.season_year AS VARCHAR), 1, 4) AS INTEGER) + 1
                   ELSE CAST(s.season_year AS INTEGER) END AS season_end_year,
              CAST(s.season_year AS VARCHAR) AS season,
              t.team_abbrev AS team,
              s.gp AS gp,
              s.min AS mp,
              s.per AS per,
              s.bpm AS bpm,
              s.vorp AS vorp,
              s.ts_pct AS ts_pct,
              s.usg_pct AS usg_pct
            FROM unified_star.fact_player_season_stats s
            LEFT JOIN unified_star.dim_team t ON t.team_id = s.team_id
            JOIN unified_star.dim_player p ON p.player_id = s.player_id
            WHERE p.bref_player_id = ?
              AND s.is_playoffs = false
              {season_filter}
            ORDER BY season_end_year DESC, t.team_abbrev NULLS LAST
            LIMIT 500
            """,
            params,
        )
        if season_end_year is not None and not rows:
            raise InvalidSeasonError(
                "Season does not resolve for player dataset",
                detail={"identifier": identifier, "season_end_year": season_end_year},
            )
    else:
        # `player_dataset_meta` normally catches this; keep type checkers happy.
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


def player_summary(conn: duckdb.DuckDBPyConnection, identifier: str) -> PlayerHubSummary:
    # TODO P1-FE-02: `hero_stats` is intentionally open-ended in OpenAPI. Keep
    # backend keys stable and add frontend runtime validation for the consumed
    # fields so a shape change fails gracefully instead of breaking rendering.
    player = _player_or_404(conn, identifier)
    seasons = fetch_dicts(
        conn,
        """
        SELECT DISTINCT SEASON AS season_end_year
        FROM api.v_canonical_player_season_totals
        WHERE PLAYER_ID = ?
        ORDER BY season_end_year DESC
        """,
        [identifier],
    )
    available_seasons = [int(row["season_end_year"]) for row in seasons]
    default_season = available_seasons[0] if available_seasons else None
    hero = fetch_one(
        conn,
        """
        SELECT
          SUM(G) AS gp,
          SUM(PTS) AS pts,
          SUM(TRB) AS reb,
          SUM(AST) AS ast,
          CASE WHEN SUM(G) > 0 THEN SUM(PTS) / SUM(G) END AS points_per_game,
          CASE WHEN SUM(G) > 0 THEN SUM(TRB) / SUM(G) END AS total_rebounds_per_game,
          CASE WHEN SUM(G) > 0 THEN SUM(AST) / SUM(G) END AS assists_per_game
        FROM api.v_canonical_player_season_totals
        WHERE PLAYER_ID = ?
        """,
        [identifier],
    ) or {}
    return PlayerHubSummary(
        identifier=identifier,
        display_name=player["full_name"],
        leagues=["NBA"],
        default_season=default_season,
        available_seasons=available_seasons,
        hero_stats=hero,
        career=player_dataset(conn, identifier, "career"),
    )


def player_csv(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    dataset: str,
    *,
    season_end_year: int | None = None,
    include_inactive_games: bool = False,
) -> str:
    # TODO P2-BE-04: move CSV export to `db/csv_export.py` and stream large
    # datasets. This in-memory path is acceptable for tiny v1 datasets but will
    # not hold up for game logs or other high-row-count projections.
    response = player_dataset(
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
