"""Dataset registry scaffold.

This module is intentionally not wired into routes yet. It documents the shape
the hardcoded player/team dataset branches should migrate toward.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DatasetOwner = Literal["player", "team"]
DatasetScope = Literal["player", "season", "team", "team_season"]


@dataclass(frozen=True)
class ProjectionColumn:
    """One API column projected from a SQL source or derived formula."""

    key: str
    sql_expression: str
    source_lineage: str | None
    is_required: bool = True


@dataclass(frozen=True)
class DatasetBinding:
    """Declarative binding from public dataset ID to SQL source contract."""

    dataset_id: str
    owner: DatasetOwner
    scope: DatasetScope
    sql_schema: str
    sql_object: str
    projections: tuple[ProjectionColumn, ...]
    default_order_by: tuple[str, ...]
    max_page_size: int
    supports_export: bool
    supports_include_inactive_games: bool


# TODO P1-BE-02: Replace hardcoded dataset branches with this registry.
# Routes should look up `DatasetBinding` by owner/scope/dataset_id, validate
# that the backing SQL object exists, compile projections from the binding, and
# apply common filters/pagination consistently.

# TODO P1-BE-06: Expand schema-drift checks beyond visible columns.
# Startup should verify every projection exists or has a declared derived
# formula, and response validation should check registered column presence,
# dtype, nullability, and format semantics.

# TODO P2-BE-01: Implement all catalogued and future player datasets.
# Add bindings for season totals, per-game stats, advanced, shooting,
# playoffs, game logs, and any player API view exposed by the catalog.

# TODO P2-BE-02: Implement all catalogued and future team datasets.
# Add bindings for franchise history, standings, team season summaries, team
# game logs, opponent stats, four factors, and lineups.

# TODO P2-BE-05: Add route pagination and sorting.
# Registry entries should declare max page size, stable order, and whether
# total row counts are cheap enough to compute exactly.

# TODO P2-DB-01: Bind the 21 `api.v_*` views to registry entries.
# Each registry entry should name the view/table it depends on so startup can
# fail fast when ETL changes drop or rename a source object.

DATASET_BINDINGS: tuple[DatasetBinding, ...] = ()

