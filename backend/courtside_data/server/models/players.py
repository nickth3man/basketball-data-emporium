"""Pydantic models for player search, summary, and datasets."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from courtside_data.server.models.common import EndpointRowsResponse


class PlayerSearchResult(BaseModel):
    """One player search hit."""

    model_config = ConfigDict(
        json_schema_extra={"description": "One player search hit."}
    )

    name: str
    identifier: str
    leagues: list[str]


class FeaturedAthlete(PlayerSearchResult):
    """Featured player card payload."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Featured player card payload."}
    )

    blurb: str | None = None


class FeaturedAthletesResponse(BaseModel):
    """Featured players for the Player Hub landing page."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Featured players for the Player Hub landing page.",
        }
    )

    athletes: list[FeaturedAthlete]


class PlayerHubSummary(BaseModel):
    """Aggregated overview payload for one player."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Aggregated overview payload for one player."}
    )

    identifier: str
    display_name: str
    leagues: list[str]
    default_season: int | None
    available_seasons: list[int]
    hero_stats: dict[str, Any]
    career: EndpointRowsResponse
