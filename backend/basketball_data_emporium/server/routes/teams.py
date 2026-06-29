"""Team Hub API routes."""

from __future__ import annotations

from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from basketball_data_emporium.db.pool import get_db
from basketball_data_emporium.queries import teams as team_queries
from basketball_data_emporium.server.errors import InvalidSearchError
from basketball_data_emporium.server.models.common import EndpointRowsResponse
from basketball_data_emporium.server.models.teams import (
    FeaturedTeamsResponse,
    TeamHubSummary,
    TeamSearchResult,
)

router = APIRouter(tags=["teams"])


@router.get(
    "/api/teams/featured",
    response_model=FeaturedTeamsResponse,
    status_code=status.HTTP_200_OK,
    summary="Featured Teams",
    operation_id="featured_teams_api_teams_featured_get",
)
def get_featured_teams(
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> FeaturedTeamsResponse:
    return team_queries.featured_teams(db)


@router.get(
    "/api/teams/search",
    response_model=list[TeamSearchResult],
    status_code=status.HTTP_200_OK,
    summary="Search Teams",
    operation_id="search_teams_api_teams_search_get",
)
def search_teams(
    term: Annotated[str, Query(min_length=1)],
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> list[TeamSearchResult]:
    if len(term.strip()) < 2:
        raise InvalidSearchError(
            "Search term must contain at least 2 non-space characters",
            detail={"term": term},
        )
    return team_queries.search_teams(db, term)


@router.get(
    "/api/teams/{identifier}/summary",
    response_model=TeamHubSummary,
    status_code=status.HTTP_200_OK,
    summary="Team Summary",
    operation_id="team_summary_api_teams_identifier_summary_get",
)
def get_team_summary(
    identifier: str,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> TeamHubSummary:
    return team_queries.team_summary(db, identifier)


@router.get(
    "/api/teams/{identifier}/seasons/{season_end_year}/{dataset}",
    response_model=EndpointRowsResponse,
    status_code=status.HTTP_200_OK,
    summary="Team Season Dataset",
    operation_id="team_season_dataset_api_teams_identifier_seasons_season_end_year_dataset_get",
)
def get_team_season_dataset(
    identifier: str,
    season_end_year: int,
    dataset: str,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
    include_inactive_games: bool = False,
) -> EndpointRowsResponse:
    return team_queries.team_dataset(
        db,
        identifier,
        dataset,
        season_end_year=season_end_year,
        include_inactive_games=include_inactive_games,
    )


@router.get(
    "/api/teams/{identifier}/export",
    status_code=status.HTTP_200_OK,
    summary="Export Team Dataset",
    operation_id="export_team_dataset_api_teams_identifier_export_get",
)
def export_team_dataset(
    identifier: str,
    dataset: Annotated[str, Query(min_length=1)],
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
    season_end_year: int | None = None,
    include_inactive_games: bool = False,
) -> StreamingResponse:
    csv_body = team_queries.team_csv(
        db,
        identifier,
        dataset,
        season_end_year=season_end_year,
        include_inactive_games=include_inactive_games,
    )
    safe_identifier = "".join(ch for ch in identifier if ch.isalnum() or ch in {"_", "-"})
    safe_dataset = "".join(ch for ch in dataset if ch.isalnum() or ch in {"_", "-"})
    return StreamingResponse(
        csv_body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_identifier}-{safe_dataset}.csv"'
        },
    )


@router.get(
    "/api/teams/{identifier}/{dataset}",
    response_model=EndpointRowsResponse,
    status_code=status.HTTP_200_OK,
    summary="Team Dataset",
    operation_id="team_dataset_api_teams_identifier_dataset_get",
)
def get_team_dataset(
    identifier: str,
    dataset: str,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> EndpointRowsResponse:
    return team_queries.team_dataset(db, identifier, dataset)
