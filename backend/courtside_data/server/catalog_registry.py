"""Static factory functions for the catalog endpoints.

Both ``build_player_hub_catalog`` and ``build_team_hub_catalog`` read
from the column manifest (the source of truth for every API column the
DB has) and build the catalog payload once at module load. The route
handlers return the cached instance — no DB query at request time.

The manifest gap
----------------
The column manifest (``backend/courtside_data/catalog/column_manifest.py``)
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

from courtside_data.catalog import by_key
from courtside_data.server.models.catalog import (
    ColumnMeta,
    DatasetCatalogEntry,
    PlayerHubCatalog,
    PlayerHubTab,
    TeamDatasetCatalogEntry,
    TeamHubCatalog,
    TeamHubTab,
)


def _build_column_meta(
    keys: list[str], default_visible: set[str]
) -> list[ColumnMeta]:
    """Build ``ColumnMeta`` from manifest keys. Fails fast on unknown keys.

    Each ``ColumnMeta.key`` is resolved against the manifest via
    :func:`courtside_data.catalog.by_key`. A typo or a key that the
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
)


_PLAYER_HUB_DATASETS: tuple[DatasetCatalogEntry, ...] = (
    # TODO P1-BE-08: split this into a true career-total dataset and a
    # per-season career-arc dataset, or rename the catalog copy. The current
    # backend response returns season rows so the frontend chart can render,
    # despite the label/description saying "Career Totals".
    DatasetCatalogEntry(
        id="career",
        label="Career Totals",
        endpoint_name="career",
        scope="player",
        description="Lifetime regular-season totals for the player.",
        columns=_build_column_meta(
            ["gp", "pts", "ast", "reb", "stl", "blk", "tov", "mp"],
            default_visible={"pts", "ast", "reb", "gp"},
        ),
        default_visible_columns=["pts", "ast", "reb", "gp"],
        supports_export=True,
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
        # TODO P2-BE-02: replace this placeholder with backed franchise
        # datasets once `db/registry.py` owns team history, standings, and
        # leader bindings. Until then, the tab intentionally advertises no
        # datasets.
        description="Franchise-level history (coming soon).",
        scope="team",
        datasets=[],
        default_dataset="",
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
    ),
)

# TODO P2-BE-08: hydrate labels, units, and formatting rules from
# `meta.canonical_metric` once the metric contract is stable. Hardcoded labels
# keep v1 simple, but they do not give downstream users DB-backed provenance.


@lru_cache(maxsize=1)
def build_team_hub_catalog() -> TeamHubCatalog:
    """Return the cached ``TeamHubCatalog`` instance."""
    return TeamHubCatalog(
        tabs=list(_TEAM_HUB_TABS),
        datasets=list(_TEAM_HUB_DATASETS),
    )
