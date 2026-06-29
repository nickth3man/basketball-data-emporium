"""Player search, summary, dataset, and export queries."""

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
    player_dataset_meta,
    season_end_expr,
)
from basketball_data_emporium.server.errors import InvalidPlayerError, InvalidSeasonError
from basketball_data_emporium.server.models.common import EndpointRowsResponse
from basketball_data_emporium.server.models.players import (
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


def validate_featured_players(conn: duckdb.DuckDBPyConnection) -> None:
    """Fail fast if a curated featured player slug no longer resolves."""
    missing: list[str] = []
    for identifier, _blurb in FEATURED_PLAYERS:
        row = fetch_one(
            conn,
            f"""
            SELECT 1
            FROM {dim_player_table("p")}
            WHERE bref_player_id = ?
            LIMIT 1
            """,
            [identifier],
        )
        if row is None:
            missing.append(identifier)
    if missing:
        raise InvalidPlayerError(
            "Featured player identifier does not resolve",
            detail={"identifiers": missing},
        )


def _player_or_404(conn: duckdb.DuckDBPyConnection, identifier: str) -> dict[str, Any]:
    player = fetch_one(
        conn,
        f"""
        SELECT player_id, bref_player_id, display_name
        FROM {dim_player_table("p")}
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
        f"""
        SELECT bref_player_id AS identifier, display_name AS name
        FROM {dim_player_table("p")}
        WHERE bref_player_id ILIKE ? OR display_name ILIKE ?
        ORDER BY
          CASE WHEN LOWER(bref_player_id) = LOWER(?) THEN 0
               WHEN LOWER(display_name) = LOWER(?) THEN 1
               WHEN display_name ILIKE ? THEN 2
               ELSE 3 END,
          bref_player_id,
          display_name
        LIMIT 20
        """,
        [pattern, pattern, term.strip(), term.strip(), f"{term.strip()}%"],
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
                name=row["display_name"],
                identifier=row["bref_player_id"],
                blurb=blurb,
                leagues=["NBA"],
            )
        )
    return FeaturedAthletesResponse(athletes=athletes)


def _career_rows(conn: duckdb.DuckDBPyConnection, identifier: str) -> list[dict[str, Any]]:
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


def _available_player_seasons(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
) -> list[int]:
    rows = fetch_dicts(
        conn,
        """
        SELECT DISTINCT SEASON AS season_end_year
        FROM api.v_canonical_player_season_totals
        WHERE PLAYER_ID = ?
        ORDER BY season_end_year DESC
        """,
        [identifier],
    )
    return [int(row["season_end_year"]) for row in rows]


def player_dataset(
    conn: duckdb.DuckDBPyConnection,
    identifier: str,
    dataset: str,
    *,
    season_end_year: int | None = None,
    include_inactive_games: bool = False,  # noqa: ARG001 - reserved for game-log datasets
) -> EndpointRowsResponse:
    _player_or_404(conn, identifier)
    endpoint_name, columns, default_visible = player_dataset_meta(dataset)
    binding = require_dataset_binding("player", dataset)
    if season_end_year is not None:
        available = _available_player_seasons(conn, identifier)
        if season_end_year not in available:
            raise InvalidSeasonError(
                "Season does not resolve for player",
                detail={
                    "identifier": identifier,
                    "season_end_year": season_end_year,
                    "available_seasons": available[:25],
                },
            )

    if dataset == "career":
        rows = _career_rows(conn, identifier)
    elif dataset == "adjusted-shooting":
        params: list[Any] = [identifier]
        season_filter = ""
        end_expr = season_end_expr("s.season_year")
        if season_end_year is not None:
            season_filter = f"AND {end_expr} = ?"
            params.append(season_end_year)
        rows = fetch_dicts(
            conn,
            f"""
            SELECT
              {end_expr} AS season_end_year,
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
            LEFT JOIN {dim_team_table("t")}
              ON t.team_id = s.team_id
             AND COALESCE(t.season_founded, 0) <= {end_expr}
             AND COALESCE(t.season_active_till, 9999) >= {end_expr}
            JOIN {dim_player_table("p")} ON p.player_id = s.player_id
            WHERE p.bref_player_id = ?
              AND s.is_playoffs = false
              {season_filter}
            ORDER BY season_end_year DESC, t.team_abbrev NULLS LAST
            LIMIT {binding.max_page_size}
            """,
            params,
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
    player = _player_or_404(conn, identifier)
    available_seasons = _available_player_seasons(conn, identifier)
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
        display_name=player["display_name"],
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
) -> Iterator[bytes]:
    response = player_dataset(
        conn,
        identifier,
        dataset,
        season_end_year=season_end_year,
        include_inactive_games=include_inactive_games,
    )
    fieldnames = [column.key for column in response.columns]
    return stream_csv_response(fieldnames, response.rows)
