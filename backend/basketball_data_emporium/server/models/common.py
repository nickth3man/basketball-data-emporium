"""Shared HTTP response models for endpoint-backed datasets."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from basketball_data_emporium.server.models.catalog import ColumnMeta


class EndpointRowsResponse(BaseModel):
    """Rows plus presentation metadata for a dataset endpoint."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Rows plus presentation metadata for a dataset endpoint.",
        }
    )

    dataset: str
    endpoint_name: str
    params: dict[str, Any]
    row_count: int
    columns: list[ColumnMeta]
    default_visible_columns: list[str]
    rows: list[dict[str, Any]]
