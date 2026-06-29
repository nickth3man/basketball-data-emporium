"""Shared Pydantic response models for the Courtside Data API.

These mirror the shapes consumed by the frontend
(`frontend/src/features/player-hub/types.ts` and
`frontend/src/lib/openapi-types.ts`) and are what the OpenAPI generator
turns into the TS `paths`/`components` blocks.

The wire envelope for every error is:

    {
      "detail": {
        "code":    "invalid_player",
        "message": "No such player 'nope'",
        "detail":  { "identifier": "nope" }
      }
    }

…which matches `frontend/src/lib/api-errors.ts:82-95`. See
`courtside_data.server.errors` for the exception hierarchy that maps
onto this envelope.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StatusResponse(BaseModel):
    """Health and runtime metadata for the UI shell.

    Returned by `GET /api/status`. `endpoint_count` is the static total
    of planned endpoints (15) — it is informational, not a live count.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Health and runtime metadata for the UI shell.",
        }
    )

    ok: bool
    endpoint_count: int


class ApiError(BaseModel):
    """Stable error envelope returned inside FastAPI's `{ detail }` body."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Stable error response returned by the Courtside Data API.",
        }
    )

    code: str = Field(..., description="One of the 8 stable error codes.")
    message: str = Field(..., description="Short human-readable summary.")
    detail: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context for the failure.",
    )
