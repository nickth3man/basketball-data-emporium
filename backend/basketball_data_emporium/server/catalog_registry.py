"""Static factory functions for the catalog endpoints.

Both ``build_player_hub_catalog`` and ``build_team_hub_catalog`` read
from the column manifest (the source of truth for every API column the
DB has) and build the catalog payload once at module load. The route
handlers return the cached instance — no DB query at request time.

The manifest gap
----------------
The column manifest (``backend/basketball_data_emporium/catalog/column_manifest.py``)
is the canonical list of API keys. A few "expected" keys in the
player-hub / team-hub briefs (``points_per_game``, ``team_name_abbr``,
``total_rebounds_per_game``, ``position``) are NOT in the manifest —
they have no declared DB lineage yet. We deliberately drop them here
so the catalog only advertises columns the API can actually resolve.
A follow-up will add the missing lineage tuples to the manifest and
re-extend the catalog lists; until then, fewer columns on the UI is
safer than promising a key that returns 500 at request time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from basketball_data_emporium.catalog import by_key
from basketball_data_emporium.db.registry import get_dataset_binding
from basketball_data_emporium.server.models.catalog import (
    ColumnMeta,
    DatasetCatalogEntry,
    PlayerHubCatalog,
    PlayerHubTab,
    TeamDatasetCatalogEntry,
    TeamHubCatalog,
    TeamHubTab,
)


def _build_column_meta(keys: list[str], default_visible: set[str]) -> list[ColumnMeta]:
    """Build ``ColumnMeta`` from manifest keys. Fails fast on unknown keys.

    Each ``ColumnMeta.key`` is resolved against the manifest via
    :func:`basketball_data_emporium.catalog.by_key`. A typo or a key that the
    manifest doesn't know about raises ``KeyError`` here (at module
    load) rather than silently emitting a stub with an empty label.
    """
    return [
        ColumnMeta(
            key=k,
            label=by_key(k).label,
            default_visible=k in default_visible,
            numeric=by_key(k).dtype in {"int", "float", "decimal"},
        )
        for k in keys
    ]


_DERIVED_COLUMN_META: dict[str, ColumnMeta] = {
    "season_end_year": ColumnMeta(key="season_end_year", label="Season", numeric=True),
    "team": ColumnMeta(key="team", label="Team", numeric=False),
    "win_pct": ColumnMeta(key="win_pct", label="Win%", numeric=True),
}


def _column_meta(keys: list[str], default_visible: set[str]) -> list[ColumnMeta]:
    columns: list[ColumnMeta] = []
    for key in keys:
        if key in _DERIVED_COLUMN_META:
            derived = _DERIVED_COLUMN_META[key]
            columns.append(
                ColumnMeta(
                    key=derived.key,
                    label=derived.label,
                    default_visible=key in default_visible,
                    numeric=derived.numeric,
                )
            )
            continue
        contract = by_key(key)
        columns.append(
            ColumnMeta(
                key=key,
                label=contract.label,
                default_visible=key in default_visible,
                numeric=contract.dtype in {"int", "float", "decimal"},
            )
        )
    return columns


def _supports_inactive_games(scope: Literal["player", "team"], dataset_id: str) -> bool:
    """Whether a dataset binding exists and can opt out of the inactive-games filter."""
    binding = get_dataset_binding(scope, dataset_id)
    return binding is not None and binding.supports_include_inactive_games


# ---------------------------------------------------------------------------
# Player-hub catalog
# ---------------------------------------------------------------------------


_PLAYER_HUB_TABS: tuple[PlayerHubTab, ...] = (
    PlayerHubTab(
        id="overview",
        label="Overview",
        description="Career-level summary for the player.",
        scope="player",
        datasets=["career"],
        default_dataset="career",
    ),
    PlayerHubTab(
        id="stats",
        label="Stats",
        description="Adjusted per-season shooting and efficiency metrics.",
        scope="season",
        datasets=["adjusted-shooting"],
        default_dataset="adjusted-shooting",
    ),
    PlayerHubTab(
        id="shooting",
        label="Shooting",
        description="Season-level field-goal, 3-point, and free-throw totals.",
        scope="player",
        datasets=["shooting"],
        default_dataset="shooting",
    ),
)


_PLAYER_HUB_DATASETS: tuple[DatasetCatalogEntry, ...] = (
    DatasetCatalogEntry(
        id="career",
        label="Season Totals",
        endpoint_name="career",
        scope="player",
        description="Regular-season totals by season and team.",
        columns=_build_column_meta(
            ["gp", "pts", "ast", "reb", "stl", "blk", "tov", "mp"],
            default_visible={"pts", "ast", "reb", "gp"},
        ),
        default_visible_columns=["pts", "ast", "reb", "gp"],
        supports_export=True,
        supports_include_inactive_games=_supports_inactive_games("player", "career"),
    ),
    DatasetCatalogEntry(
        id="adjusted-shooting",
        label="Adjusted Shooting",
        endpoint_name="adjusted_shooting",
        scope="season",
        description="Per-season advanced efficiency metrics (PER, BPM, VORP, TS%, USG%).",
        columns=_build_column_meta(
            ["gp", "mp", "per", "bpm", "vorp", "ts_pct", "usg_pct"],
            default_visible={"per", "bpm", "vorp", "gp"},
        ),
        default_visible_columns=["per", "bpm", "vorp", "gp"],
        supports_export=True,
        supports_include_inactive_games=_supports_inactive_games(
            "player", "adjusted-shooting"
        ),
    ),
    DatasetCatalogEntry(
        id="shooting",
        label="Shooting",
        endpoint_name="shooting",
        scope="player",
        description="Regular-season shooting totals and percentages by season and team.",
        columns=_column_meta(
            [
                "season_end_year",
                "team",
                "fgm",
                "fga",
                "fg_pct",
                "fg3m",
                "fg3a",
                "fg3_pct",
                "ftm",
                "fta",
                "ft_pct",
            ],
            default_visible={"season_end_year", "team", "fgm", "fga", "fg_pct", "fg3m"},
        ),
        default_visible_columns=["season_end_year", "team", "fgm", "fga", "fg_pct", "fg3m"],
        supports_export=True,
        supports_include_inactive_games=_supports_inactive_games("player", "shooting"),
    ),
)


@lru_cache(maxsize=1)
def build_player_hub_catalog() -> PlayerHubCatalog:
    """Return the cached ``PlayerHubCatalog`` instance.

    Built once at first call; the same instance is returned to every
    caller thereafter. Route handlers can call this on every request
    without any DB work.
    """
    return PlayerHubCatalog(
        tabs=list(_PLAYER_HUB_TABS),
        datasets=list(_PLAYER_HUB_DATASETS),
    )


# ---------------------------------------------------------------------------
# Team-hub catalog
# ---------------------------------------------------------------------------


_TEAM_HUB_TABS: tuple[TeamHubTab, ...] = (
    TeamHubTab(
        id="overview",
        label="Overview",
        description="Roster snapshot for the team.",
        scope="team",
        datasets=["roster"],
        default_dataset="roster",
    ),
    TeamHubTab(
        id="franchise",
        label="Franchise",
        description="Franchise-level season history.",
        scope="team",
        datasets=["franchise-arc"],
        default_dataset="franchise-arc",
    ),
)


_TEAM_HUB_DATASETS: tuple[TeamDatasetCatalogEntry, ...] = (
    TeamDatasetCatalogEntry(
        id="roster",
        label="Roster",
        endpoint_name="team_roster",
        scope="team",
        description="Current roster with playing-time and efficiency aggregates.",
        columns=_build_column_meta(
            ["full_name", "gp", "mp", "per"],
            default_visible={"full_name", "per"},
        ),
        default_visible_columns=["full_name", "per"],
        supports_export=True,
        supports_include_inactive_games=_supports_inactive_games("team", "roster"),
    ),
    TeamDatasetCatalogEntry(
        id="franchise-arc",
        label="Franchise Arc",
        endpoint_name="franchise_arc",
        scope="team",
        description="Season-by-season franchise wins, losses, and win percentage.",
        columns=_column_meta(
            ["season_end_year", "wins", "losses", "win_pct"],
            default_visible={"season_end_year", "wins", "losses", "win_pct"},
        ),
        default_visible_columns=["season_end_year", "wins", "losses", "win_pct"],
        supports_export=True,
        supports_include_inactive_games=False,
    ),
)


@lru_cache(maxsize=1)
def build_team_hub_catalog() -> TeamHubCatalog:
    """Return the cached ``TeamHubCatalog`` instance."""
    return TeamHubCatalog(
        tabs=list(_TEAM_HUB_TABS),
        datasets=list(_TEAM_HUB_DATASETS),
    )
