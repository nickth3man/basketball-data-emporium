"""Season Hub API routes."""

from __future__ import annotations

from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, Query, status

from basketball_data_emporium.queries import seasons as season_queries
from basketball_data_emporium.server.deps import get_db
from basketball_data_emporium.server.models.common import EndpointRowsResponse
from basketball_data_emporium.server.models.seasons import AvailableSeasonsResponse

router = APIRouter(tags=["seasons"])


@router.get(
    "/api/seasons",
    response_model=AvailableSeasonsResponse,
    status_code=status.HTTP_200_OK,
    summary="Available Seasons",
    operation_id="available_seasons_api_seasons_get",
)
def get_available_seasons(
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> AvailableSeasonsResponse:
    return season_queries.available_seasons(db)


@router.get(
    "/api/seasons/{season_end_year}/standings",
    response_model=EndpointRowsResponse,
    status_code=status.HTTP_200_OK,
    summary="Season Standings",
    operation_id="season_standings_api_seasons_season_end_year_standings_get",
)
def get_season_standings(
    season_end_year: int,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
) -> EndpointRowsResponse:
    return season_queries.season_standings(db, season_end_year)


@router.get(
    "/api/seasons/{season_end_year}/leaders",
    response_model=EndpointRowsResponse,
    status_code=status.HTTP_200_OK,
    summary="Season Leaders",
    operation_id="season_leaders_api_seasons_season_end_year_leaders_get",
)
def get_season_leaders(
    season_end_year: int,
    db: Annotated[duckdb.DuckDBPyConnection, Depends(get_db)],
    season_type: Annotated[season_queries.SeasonType, Query()] = "Regular",
    stat: Annotated[season_queries.LeaderStat, Query()] = "pts",
) -> EndpointRowsResponse:
    return season_queries.season_leaders(
        db,
        season_end_year,
        season_type=season_type,
        stat=stat,
    )
