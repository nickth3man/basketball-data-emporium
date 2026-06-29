"""Player Hub API routes."""

from __future__ import annotations

from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, Query, Response, status

from courtside_data.db.pool import get_db
from courtside_data.queries import players as player_queries
from courtside_data.server.errors import InvalidSearchError
from courtside_data.server.models.common import EndpointRowsResponse
from courtside_data.server.models.players import (
    FeaturedAthletesResponse,
    PlayerHubSummary,
    PlayerSearchResult,
)

router = APIRouter(tags=["players"])


@router.get(
    "/api/players/featured",
    response_model=FeaturedAthletesResponse,
    status_code=status.HTTP_200_OK,
    summary="Featured Players",
    operation_id="featured_players_api_players_featured_get",
)
def get_featured_players(
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> FeaturedAthletesResponse:
    return player_queries.featured_players(db)


@router.get(
    "/api/players/search",
    response_model=list[PlayerSearchResult],
    status_code=status.HTTP_200_OK,
    summary="Search Players",
    operation_id="search_players_api_players_search_get",
)
def search_players(
    term: Annotated[str, Query(min_length=1)],
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> list[PlayerSearchResult]:
    if len(term.strip()) < 2:
        raise InvalidSearchError(
            "Search term must contain at least 2 non-space characters",
            detail={"term": term},
        )
    return player_queries.search_players(db, term)


@router.get(
    "/api/players/{identifier}/summary",
    response_model=PlayerHubSummary,
    status_code=status.HTTP_200_OK,
    summary="Player Summary",
    operation_id="player_summary_api_players_identifier_summary_get",
)
def get_player_summary(
    identifier: str,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> PlayerHubSummary:
    return player_queries.player_summary(db, identifier)


@router.get(
    "/api/players/{identifier}/seasons/{season_end_year}/{dataset}",
    response_model=EndpointRowsResponse,
    status_code=status.HTTP_200_OK,
    summary="Player Season Dataset",
    operation_id="player_season_dataset_api_players_identifier_seasons_season_end_year_dataset_get",
)
def get_player_season_dataset(
    identifier: str,
    season_end_year: int,
    dataset: str,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
    include_inactive_games: bool = False,
) -> EndpointRowsResponse:
    return player_queries.player_dataset(
        db,
        identifier,
        dataset,
        season_end_year=season_end_year,
        include_inactive_games=include_inactive_games,
    )


@router.get(
    "/api/players/{identifier}/export",
    status_code=status.HTTP_200_OK,
    summary="Export Player Dataset",
    operation_id="export_player_dataset_api_players_identifier_export_get",
)
def export_player_dataset(
    identifier: str,
    dataset: Annotated[str, Query(min_length=1)],
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
    season_end_year: int | None = None,
    include_inactive_games: bool = False,
) -> Response:
    csv_body = player_queries.player_csv(
        db,
        identifier,
        dataset,
        season_end_year=season_end_year,
        include_inactive_games=include_inactive_games,
    )
    safe_identifier = "".join(ch for ch in identifier if ch.isalnum() or ch in {"_", "-"})
    safe_dataset = "".join(ch for ch in dataset if ch.isalnum() or ch in {"_", "-"})
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_identifier}-{safe_dataset}.csv"'
        },
    )


@router.get(
    "/api/players/{identifier}/{dataset}",
    response_model=EndpointRowsResponse,
    status_code=status.HTTP_200_OK,
    summary="Player Dataset",
    operation_id="player_dataset_api_players_identifier_dataset_get",
)
def get_player_dataset(
    identifier: str,
    dataset: str,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> EndpointRowsResponse:
    return player_queries.player_dataset(db, identifier, dataset)
