"""Pydantic models for team search, summary, and datasets."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from courtside_data.server.models.common import EndpointRowsResponse


class TeamSearchResult(BaseModel):
    """One team search hit."""

    model_config = ConfigDict(json_schema_extra={"description": "One team search hit."})

    name: str
    identifier: str
    leagues: list[str]


class FeaturedTeam(TeamSearchResult):
    """Featured team card payload."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Featured team card payload."}
    )

    blurb: str | None = None


class FeaturedTeamsResponse(BaseModel):
    """Featured teams for the Team Hub landing page."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Featured teams for the Team Hub landing page."}
    )

    teams: list[FeaturedTeam]


class TeamHeroStats(BaseModel):
    """Current/default season headline metrics for a team."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Current/default season headline metrics for a team.",
        }
    )

    team: str
    season: int | str | None = None
    wins: int | None = None
    losses: int | None = None
    win_pct: float | None = None
    off_rtg: float | None = None
    def_rtg: float | None = None


class FranchiseArcPoint(BaseModel):
    """One season on a franchise win/loss arc."""

    model_config = ConfigDict(
        json_schema_extra={"description": "One season on a franchise win/loss arc."}
    )

    season_end_year: int
    team_name: str | None = None
    wins: int | None = None
    losses: int | None = None
    win_pct: float | None = None


class TeamHubSummary(BaseModel):
    """Aggregated overview payload for one team."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Aggregated overview payload for one team."}
    )

    identifier: str
    display_name: str
    leagues: list[str]
    default_season: int | None
    available_seasons: list[int]
    hero_stats: TeamHeroStats
    roster: EndpointRowsResponse
    franchise_arc: list[FranchiseArcPoint]
