"""Pydantic schemas shared by the Basketball Data Emporium API."""

from basketball_data_emporium.schemas.common import (
    ApiError,
    StatusResponse,
)

__all__ = ["ApiError", "StatusResponse"]
