"""Pydantic models for the static catalog endpoints.

Two endpoints share this module:

* ``GET /api/endpoints/player-hub`` — ``PlayerHubCatalog``
* ``GET /api/endpoints/team-hub``    — ``TeamHubCatalog``

Both responses are built once at module load (see
:mod:`basketball_data_emporium.server.catalog_registry`) and served as-is.
There is no per-request database query.

Why two scope literals
----------------------
The two hubs speak slightly different scopes:

* The player hub's natural scopes are ``"player"`` (a career summary
  that doesn't depend on a season) and ``"season"`` (a per-season
  statline).
* The team hub's natural scopes are ``"team"`` (a franchise-level
  view such as the roster snapshot) and ``"team_season"`` (a
  per-season team summary).

Modelling them as two distinct ``Literal[...]`` aliases keeps the
OpenAPI schema precise (``scope: "player" | "season"`` on player
types, ``scope: "team" | "team_season"`` on team types) so the
frontend can't accidentally pass the wrong discriminator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DatasetScope = Literal["player", "season"]
TeamDatasetScope = Literal["team", "team_season"]


class ColumnMeta(BaseModel):
    """Presentation metadata for one response column."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Presentation metadata for one response column.",
        }
    )

    key: str
    label: str
    default_visible: bool = True
    numeric: bool = False


class PlayerHubTab(BaseModel):
    """A tab and its backing datasets in the Player Hub."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "A tab and its backing datasets in the Player Hub.",
        }
    )

    id: str
    label: str
    description: str
    scope: DatasetScope
    datasets: list[str]
    default_dataset: str


class DatasetCatalogEntry(BaseModel):
    """HTTP-facing metadata for one Player Hub dataset."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "HTTP-facing metadata for one Player Hub dataset.",
        }
    )

    id: str
    label: str
    endpoint_name: str
    scope: DatasetScope
    description: str
    columns: list[ColumnMeta]
    default_visible_columns: list[str] = Field(default_factory=list)
    supports_export: bool = True
    supports_include_inactive_games: bool = False


class PlayerHubCatalog(BaseModel):
    """Top-level Player Hub catalog response: tabs and dataset metadata for the player-hub UI shell."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Top-level Player Hub catalog response: tabs and dataset metadata for the player-hub UI shell.",
        }
    )

    tabs: list[PlayerHubTab]
    datasets: list[DatasetCatalogEntry]


class TeamHubTab(BaseModel):
    """A tab and its backing datasets in the Team Hub."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "A tab and its backing datasets in the Team Hub.",
        }
    )

    id: str
    label: str
    description: str
    scope: TeamDatasetScope
    datasets: list[str]
    default_dataset: str


class TeamDatasetCatalogEntry(BaseModel):
    """HTTP-facing metadata for one Team Hub dataset.

    Mirrors :class:`DatasetCatalogEntry` but uses the team-scope literal
    (``"team"`` / ``"team_season"``) and treats ``columns`` and
    ``default_visible_columns`` as optional (some team-hub tabs are
    navigation-only and don't carry a column manifest).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "description": "HTTP-facing metadata for one Team Hub dataset.",
        }
    )

    id: str
    label: str
    endpoint_name: str
    scope: TeamDatasetScope
    description: str
    columns: list[ColumnMeta] | None = None
    default_visible_columns: list[str] | None = None
    supports_export: bool = True
    supports_include_inactive_games: bool = False


class TeamHubCatalog(BaseModel):
    """Top-level Team Hub catalog response: tabs and dataset metadata for the team-hub UI shell."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Top-level Team Hub catalog response: tabs and dataset metadata for the team-hub UI shell.",
        }
    )

    tabs: list[TeamHubTab]
    datasets: list[TeamDatasetCatalogEntry]
