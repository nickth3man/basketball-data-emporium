"""Pydantic models for league/season API routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AvailableSeasonsResponse(BaseModel):
    """Available season-ending years for the Season Hub."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Available season-ending years for the Season Hub.",
        }
    )

    seasons: list[int]
    default_season: int | None = None
