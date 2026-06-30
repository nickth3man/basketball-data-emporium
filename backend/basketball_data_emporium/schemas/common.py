"""Shared Pydantic response models for the Basketball Data Emporium API.

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
`basketball_data_emporium.server.errors` for the exception hierarchy that maps
onto this envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StatusResponse(BaseModel):
    """Health and runtime metadata for the UI shell.

    Returned by `GET /api/status`. `endpoint_count` is the static total
    of public endpoints (18) — it is informational, not a live count.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Health and runtime metadata for the UI shell.",
        }
    )

    ok: bool
    endpoint_count: int
    data_state: Literal["passed", "failed", "stale", "unverified"]
    data_state_reason: Literal[
        "audit_missing",
        "latest_pipeline_failed",
        "latest_dq_failed",
        "audit_stale",
        "dq_stale",
        "dq_missing",
        "verified",
        "unverified",
    ]
    data_verified: bool
    data_stale: bool
    latest_pipeline_run_id: str | None = None
    latest_pipeline_stage: str | None = None
    latest_pipeline_status: str | None = None
    latest_pipeline_started_at: datetime | None = None
    latest_dq_status: str | None = None


class ApiError(BaseModel):
    """Stable error envelope returned inside FastAPI's `{ detail }` body."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Stable error response returned by the Basketball Data Emporium API.",
        }
    )

    code: str = Field(..., description="One of the 8 stable error codes.")
    message: str = Field(..., description="Short human-readable summary.")
    detail: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context for the failure.",
    )
