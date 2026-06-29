"""`GET /api/endpoints/player-hub` and `GET /api/endpoints/team-hub`.

Both endpoints return a static catalog payload built once at module
load (see :mod:`courtside_data.server.catalog_registry`). There is no
per-request database query — the column manifest is the only source
of truth, and it's read at import time.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from courtside_data.server.catalog_registry import (
    build_player_hub_catalog,
    build_team_hub_catalog,
)
from courtside_data.server.models.catalog import (
    PlayerHubCatalog,
    TeamHubCatalog,
)

router = APIRouter(tags=["catalog"])


@router.get(
    "/api/endpoints/player-hub",
    response_model=PlayerHubCatalog,
    status_code=status.HTTP_200_OK,
    summary="Catalog",
    operation_id="catalog_api_endpoints_player_hub_get",
)
def get_catalog_player_hub() -> PlayerHubCatalog:
    return build_player_hub_catalog()


@router.get(
    "/api/endpoints/team-hub",
    response_model=TeamHubCatalog,
    status_code=status.HTTP_200_OK,
    summary="Team Catalog",
    description="Static Team Hub catalog: tabs and dataset metadata.",
    operation_id="team_catalog_api_endpoints_team_hub_get",
)
def get_team_catalog() -> TeamHubCatalog:
    return build_team_hub_catalog()
