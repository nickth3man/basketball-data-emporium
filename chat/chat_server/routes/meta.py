"""Meta routes: health and public config.

Routes are mounted under the `/api` prefix in `chat_server.main` so the
paths here are bare (`/health`, `/config`).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import get_settings
from ..db import check_connection

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    """Response shape for `GET /api/health`."""

    status: str
    db: str


class LatencyTiers(BaseModel):
    """Latency budget per template complexity tier."""

    simple_seconds: tuple[int, int]
    medium_seconds: tuple[int, int]
    heavy_seconds: tuple[int, int]
    heavy_timeout_seconds: int


class ConfigResponse(BaseModel):
    """Public (non-secret) runtime config surfaced at `GET /api/config`."""

    openrouter_model: str
    latency_tiers: LatencyTiers


_LATENCY_TIERS = LatencyTiers(
    simple_seconds=(1, 5),
    medium_seconds=(5, 20),
    heavy_seconds=(20, 120),
    heavy_timeout_seconds=300,
)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return app + warehouse status.

    Always returns HTTP 200 so monitoring can detect the process even when
    the warehouse is degraded. The `db` field reports the warehouse status.
    """
    db_ok = check_connection()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db="connected" if db_ok else "disconnected",
    )


@router.get("/config", response_model=ConfigResponse)
def config() -> ConfigResponse:
    """Return non-secret runtime config to the frontend."""
    settings = get_settings()
    return ConfigResponse(
        openrouter_model=settings.openrouter_model,
        latency_tiers=_LATENCY_TIERS,
    )
