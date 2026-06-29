"""Dataset registry for public API dataset bindings."""

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
    format_rule: str | None = None
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
    derived_lineage: tuple[str, ...] = ()


DATASET_BINDINGS: tuple[DatasetBinding, ...] = (
    DatasetBinding(
        dataset_id="career",
        owner="player",
        scope="player",
        sql_schema="api",
        sql_object="v_canonical_player_season_totals",
        projections=(
            ProjectionColumn("season_end_year", "SEASON", "api.v_canonical_player_season_totals.SEASON"),
            ProjectionColumn("season", "season label formula", None, "season_label"),
            ProjectionColumn("team", "TEAM_ABBR", "api.v_canonical_player_season_totals.TEAM_ABBR"),
            ProjectionColumn("league", "LEAGUE", "api.v_canonical_player_season_totals.LEAGUE"),
            ProjectionColumn("gp", "G", "api.v_canonical_player_season_totals.G"),
            ProjectionColumn("mp", "MP", "api.v_canonical_player_season_totals.MP"),
            ProjectionColumn("pts", "PTS", "api.v_canonical_player_season_totals.PTS"),
            ProjectionColumn("reb", "TRB", "api.v_canonical_player_season_totals.TRB"),
            ProjectionColumn("ast", "AST", "api.v_canonical_player_season_totals.AST"),
            ProjectionColumn("stl", "STL", "api.v_canonical_player_season_totals.STL"),
            ProjectionColumn("blk", "BLK", "api.v_canonical_player_season_totals.BLK"),
            ProjectionColumn("tov", "TOV", "api.v_canonical_player_season_totals.TOV"),
            ProjectionColumn("points_per_game", "PTS / G", None, "per_game_rate"),
            ProjectionColumn("total_rebounds_per_game", "TRB / G", None, "per_game_rate"),
            ProjectionColumn("assists_per_game", "AST / G", None, "per_game_rate"),
        ),
        default_order_by=("season_end_year DESC", "team"),
        max_page_size=500,
        supports_export=True,
        supports_include_inactive_games=False,
        derived_lineage=(
            "season = formatted ending-season label from SEASON",
            "points_per_game = PTS / G when G > 0",
            "total_rebounds_per_game = TRB / G when G > 0",
            "assists_per_game = AST / G when G > 0",
        ),
    ),
    DatasetBinding(
        dataset_id="adjusted-shooting",
        owner="player",
        scope="season",
        sql_schema="unified_star",
        sql_object="fact_player_season_stats",
        projections=(
            ProjectionColumn("season_end_year", "season_year normalized to ending year", None, "season_end_year"),
            ProjectionColumn("season", "season_year", "unified_star.fact_player_season_stats.season_year"),
            ProjectionColumn("team", "team_abbrev", "unified_star.dim_team.team_abbrev"),
            ProjectionColumn("gp", "gp", "unified_star.fact_player_season_stats.gp"),
            ProjectionColumn("mp", "min", "unified_star.fact_player_season_stats.min"),
            ProjectionColumn("per", "per", "unified_star.fact_player_season_stats.per"),
            ProjectionColumn("bpm", "bpm", "unified_star.fact_player_season_stats.bpm"),
            ProjectionColumn("vorp", "vorp", "unified_star.fact_player_season_stats.vorp"),
            ProjectionColumn("ts_pct", "ts_pct", "unified_star.fact_player_season_stats.ts_pct"),
            ProjectionColumn("usg_pct", "usg_pct", "unified_star.fact_player_season_stats.usg_pct"),
        ),
        default_order_by=("season_end_year DESC", "team"),
        max_page_size=500,
        supports_export=True,
        supports_include_inactive_games=False,
        derived_lineage=("season_end_year = normalized ending year from season_year",),
    ),
    DatasetBinding(
        dataset_id="roster",
        owner="team",
        scope="team",
        sql_schema="unified_star",
        sql_object="fact_player_season_stats",
        projections=(
            ProjectionColumn("full_name", "full_name", "unified_star.dim_player.full_name"),
            ProjectionColumn("bref_player_id", "bref_player_id", "unified_star.dim_player.bref_player_id"),
            ProjectionColumn("season_end_year", "season_year normalized to ending year", None, "season_end_year"),
            ProjectionColumn("gp", "gp", "unified_star.fact_player_season_stats.gp"),
            ProjectionColumn("mp", "min", "unified_star.fact_player_season_stats.min"),
            ProjectionColumn("per", "per", "unified_star.fact_player_season_stats.per"),
        ),
        default_order_by=("mp DESC NULLS LAST", "full_name"),
        max_page_size=500,
        supports_export=True,
        supports_include_inactive_games=False,
        derived_lineage=("season_end_year = normalized ending year from season_year",),
    ),
    DatasetBinding(
        dataset_id="franchise-arc",
        owner="team",
        scope="team",
        sql_schema="unified_star",
        sql_object="fact_team_season_summary",
        projections=(
            ProjectionColumn("season_end_year", "season_year normalized to ending year", None, "season_end_year"),
            ProjectionColumn("wins", "w", "unified_star.fact_team_season_summary.w"),
            ProjectionColumn("losses", "l", "unified_star.fact_team_season_summary.l"),
            ProjectionColumn("win_pct", "w / (w + l)", None, "fraction"),
        ),
        default_order_by=("season_end_year",),
        max_page_size=500,
        supports_export=True,
        supports_include_inactive_games=False,
        derived_lineage=(
            "season_end_year = normalized ending year from season_year",
            "win_pct = w / (w + l) when w + l > 0",
        ),
    ),
)


def get_dataset_binding(owner: DatasetOwner, dataset_id: str) -> DatasetBinding | None:
    """Return the binding for a public dataset ID, if registered."""
    for binding in DATASET_BINDINGS:
        if binding.owner == owner and binding.dataset_id == dataset_id:
            return binding
    return None


def require_dataset_binding(owner: DatasetOwner, dataset_id: str) -> DatasetBinding:
    """Return a binding or raise ``KeyError`` with a stable message."""
    binding = get_dataset_binding(owner, dataset_id)
    if binding is None:
        raise KeyError(f"Unknown {owner} dataset: {dataset_id}")
    return binding
