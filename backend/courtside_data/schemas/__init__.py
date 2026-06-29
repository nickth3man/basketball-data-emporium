"""Pydantic schemas shared by the Courtside Data API."""

from courtside_data.schemas.common import (
    ApiError,
    StatusResponse,
)

__all__ = ["ApiError", "StatusResponse"]
