"""Compact trusted schema map for the Pydantic AI agent.

Built once at startup, cached for the agent's lifetime. The agent receives
this map as part of its system prompt so it can pick templates and name
canonical tables/metrics/gaps without ever writing SQL.

Key contracts
-------------
* `ALLOWED_TABLES_FOR_AGENT` — the agent's VIEW of the warehouse. This is
  deliberately narrower than the full catalog: legacy / empty-endpoint /
  duplicate-superseded tables are NOT in this set, so the schema-context
  prompt also implicitly tells the agent those tables are off-limits.
* `SchemaContext.table_purposes` — one-line purpose + key columns + grain
  per allowed table. Capped at ~12 columns shown per table so the
  rendered prompt text stays small (a few KB).
* `SchemaContext.metric_definitions` — from `meta_metric_definition`.
* `SchemaContext.known_gaps` — from `meta_known_gap` with status NOT IN
  ('resolved'); surfaced as caveats in the agent's view.
* `SchemaContext.table_fate_excluded` — names of legacy / empty /
  duplicate tables to mention in the prompt as MUST-NOT-QUERY, so the
  agent doesn't try to use them.

Public surface
--------------
* `ALLOWED_TABLES_FOR_AGENT` — the allowlist set.
* `SchemaContext` — dataclass with `as_prompt_text()`.
* `build_schema_context(db)` — build once against an injected DB
  (used by tests; production calls `await get_schema_context()`).
* `get_schema_context()` — async singleton built from `get_db()`.
  Read-only queries against the warehouse.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .db import DuckDBSingleton, get_db

logger = logging.getLogger(__name__)


ALLOWED_TABLES_FOR_AGENT: frozenset[str] = frozenset(
    {
        "mart_player_season",
        "mart_player_career",
        "mart_draft_value",
        "mart_franchise_leaders",
        "mart_league_leaders",
        "mart_head_to_head",
        "mart_betting_summary",
        "mart_player_rolling",
        "mart_shot_zones",
        "dim_player",
        "dim_team",
        "dim_team_era",
        "dim_game",
        "dim_official",
        "dim_arena",
        "dim_date",
        "fact_player_game_box",
        "fact_player_game_advanced",
        "fact_game_result",
        "fact_pbp_event",
        "fact_shot",
        "fact_standings",
        "fact_award",
        "fact_draft",
        "fact_coach_season",
        "fact_official_assignment",
        "src_bref_advanced",
        "src_bref_per_100_poss",
        "src_fact_bref_team_season_summary",
    }
)


_EXCLUDED_FATES: frozenset[str] = frozenset(
    {"legacy_do_not_use", "duplicate_superseded", "empty_endpoint_shell"}
)


_TABLE_PURPOSES: dict[str, str] = {
    "mart_player_season": (
        "One row per (player, team, season_year, season_type). Player season aggregates."
    ),
    "mart_player_career": "One row per player. Career totals/averages across all seasons.",
    "mart_draft_value": "Draft pick value, career outcome, and value-vs-pick metrics.",
    "mart_franchise_leaders": "Career leaders per (team, stat) — franchise top-N lists.",
    "mart_league_leaders": "League top-N leaders per season and stat.",
    "mart_head_to_head": "Per (team_a, team_b) head-to-head aggregates across seasons.",
    "mart_betting_summary": "Pre-game betting summaries (spread, total, moneyline).",
    "mart_player_rolling": "Rolling-window player aggregates (e.g. last 10/20 games).",
    "mart_shot_zones": "Per (player, season, shot_zone) shot distribution and accuracy.",
    "dim_player": "Canonical player dim: full_name, birth_date, country, draft info, BBR ids.",
    "dim_team": "Canonical team dim: team_id, abbreviation, nickname, city, conference.",
    "dim_team_era": "Team-era rows: franchise names/cities/abbreviations over time.",
    "dim_game": "Canonical game dim: game_id, game_date, season_year, season_type, teams.",
    "dim_official": "Officials dimension.",
    "dim_arena": "Arenas dimension (city, opened, capacity).",
    "dim_date": "Date dimension (calendar metadata).",
    "fact_player_game_box": (
        "One row per (player, game). Box-score + is_win/is_home/opponent_team_id. "
        "Self-join on game_id for player-vs-player co-appearance (shared-game record)."
    ),
    "fact_player_game_advanced": (
        "Advanced per-game: off/def/net rating, pace, ts_pct, poss, fta_rate."
    ),
    "fact_game_result": "Final scores + per-team result per game_id.",
    "fact_pbp_event": (
        "Play-by-play events: period, clock, action_type, sub_type, shot result, scores."
    ),
    "fact_shot": "Shot-level rows: shot_zone_basic/area/range, loc_x/y, shot_made_flag.",
    "fact_standings": "League standings (W/L, conference rank) by season_year.",
    "fact_award": "Player awards (MVP, All-NBA, etc.) by season_year.",
    "fact_draft": "Draft picks (player_id, team_id, round, number, organization_type).",
    "fact_coach_season": "One row per (coach, team, season_year).",
    "fact_official_assignment": "One row per (official, game).",
    "src_bref_advanced": "Basketball-Reference advanced stats (WS, BPM, VORP). Source-backed.",
    "src_bref_per_100_poss": "Basketball-Reference per-100-possessions stats. Source-backed.",
    "src_fact_bref_team_season_summary": (
        "Basketball-Reference team season summaries (w/l, pythagorean wins/losses, "
        "offensive/defensive/net ratings, pace, true-shooting, eFG, attendance). "
        "Grain: (team_id, season). is_playoffs is a playoff-qualification flag "
        "(TRUE/FALSE), NOT a separate postseason row. Source-backed."
    ),
}


def _allowed_table_section(table_purposes: dict[str, str]) -> list[str]:
    lines = ["ALLOWED WAREHOUSE TABLES (only these tables may be queried):"]
    if not table_purposes:
        return [*lines, "  (none discovered)"]
    return [*lines, *(f"  - {name}: {purpose}" for name, purpose in table_purposes.items())]


def _metric_section(metrics: list[dict]) -> list[str]:
    if not metrics:
        return []

    lines = ["METRIC DEFINITIONS (from meta_metric_definition):"]
    for metric in metrics:
        tail_bits = [
            f"{label}={value}"
            for label, value in (
                ("expr", metric.get("expression") or ""),
                ("src", metric.get("source_priority") or ""),
                ("notes", metric.get("notes") or ""),
            )
            if value
        ]
        metric_key = metric.get("metric_key", "?")
        grain = metric.get("grain") or "?"
        tail = f" — {'; '.join(tail_bits)}" if tail_bits else ""
        lines.append(f"  - {metric_key} [grain={grain}]{tail}")
    return lines


def _known_gap_section(gaps: list[dict]) -> list[str]:
    if not gaps:
        return []

    lines = ["KNOWN GAPS (caveats to surface in answers when relevant):"]
    for gap in gaps:
        gap_key = gap.get("gap_key", "?")
        severity = gap.get("severity") or "info"
        area = gap.get("affected_area") or ""
        status = gap.get("status") or ""
        details = gap.get("details") or ""
        line = f"  - {gap_key} [{severity}, {status}, area={area}]"
        lines.append(f"{line} — {details}" if details else line)
    return lines


def _excluded_table_section(table_names: set[str]) -> list[str]:
    if not table_names:
        return []
    return [
        "EXCLUDED TABLES (MUST NOT be queried — legacy/empty/duplicate; "
        "only mentioned when explaining warehouse state):",
        *(f"  - {name}" for name in sorted(table_names)),
    ]


@dataclass
class SchemaContext:
    """Compact trusted schema map fed to the agent as system-prompt context.

    Attributes
    ----------
    table_purposes
        Mapping `table_name -> one-line purpose + key columns + grain`.
        Order is preserved; the prompt renders in insertion order (Python
        dicts are insertion-ordered since 3.7).
    metric_definitions
        Each entry is a dict matching the `meta_metric_definition` row:
        `{metric_key, grain, expression, source_priority, notes}`. Empty
        list if the table is missing.
    known_gaps
        Each entry is a dict matching `meta_known_gap` with
        `status NOT IN ('resolved')`: `{gap_key, severity, affected_area,
        status, details, recommended_action}`.
    table_fate_excluded
        Names of tables whose `meta_table_fate` row has an excluded fate
        (`legacy_do_not_use` / `duplicate_superseded` /
        `empty_endpoint_shell`). Surfaced in the prompt as MUST-NOT-QUERY.
    """

    table_purposes: dict[str, str] = field(default_factory=dict)
    metric_definitions: list[dict] = field(default_factory=list)
    known_gaps: list[dict] = field(default_factory=list)
    table_fate_excluded: set[str] = field(default_factory=set)

    def as_prompt_text(self) -> str:
        """Render the schema context as a compact text block.

        The output is intended to be embedded into the agent's system
        prompt. Kept small (a few KB) so the prompt stays under typical
        model context windows even with multiple turns.

        Sections, in order:
          1. Allowed tables (one line each): "  table_name — purpose.
             cols: a, b, c, ...; grain: ...".
          2. Metric definitions (one line each).
          3. Known gaps (caveats).
          4. Excluded tables (must-not-query).
        """
        sections = (
            _allowed_table_section(self.table_purposes),
            _metric_section(self.metric_definitions),
            _known_gap_section(self.known_gaps),
            _excluded_table_section(self.table_fate_excluded),
        )
        return "\n\n".join("\n".join(section) for section in sections if section)


async def _describe_tables(db: DuckDBSingleton) -> dict[str, str]:
    """Build per-table one-liners for `ALLOWED_TABLES_FOR_AGENT`.

    For each table in the allowlist, query `information_schema.columns`
    and produce `'table (grain guess): col1, col2, ...'`. Column list is
    capped at 12; truncation is marked with '...' so the prompt stays
    small. Missing tables (table dropped from the warehouse between
    doc and build) are reported with '(missing from warehouse)'.

    The grain "guess" is a static hint from `_TABLE_PURPOSES` rather than
    a true warehouse grain detection — the prompt's purpose is to
    orient the agent, not to be a perfect catalog.
    """
    if not ALLOWED_TABLES_FOR_AGENT:
        return {}

    try:
        result = await db.execute(
            """
            SELECT table_name, column_name, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ANY($tables)
            ORDER BY table_name, ordinal_position
            """,
            {"tables": list(ALLOWED_TABLES_FOR_AGENT)},
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("schema_context: failed to read information_schema.columns: %s", exc)
        return {}

    by_table: dict[str, list[str]] = {}
    for row in result.rows:
        name = row.get("table_name")
        col = row.get("column_name")
        if isinstance(name, str) and isinstance(col, str):
            by_table.setdefault(name, []).append(col)

    out: dict[str, str] = {}
    for tbl in sorted(ALLOWED_TABLES_FOR_AGENT):
        cols = by_table.get(tbl, [])
        if not cols:
            out[tbl] = f"{tbl} (missing from warehouse)"
            continue
        cap = 12
        shown = cols[:cap]
        suffix = "" if len(cols) <= cap else f", ... (+{len(cols) - cap} more)"
        purpose = _TABLE_PURPOSES.get(tbl, "Warehouse table.")
        out[tbl] = f"{purpose}  cols: {', '.join(shown)}{suffix}."
    return out


async def _load_metric_definitions(db: DuckDBSingleton) -> list[dict]:
    """Load every row from `meta_metric_definition`.

    Returns a list of dicts in arbitrary order; the caller sorts before
    rendering. Empty list if the table doesn't exist (older builds) or
    on read errors.
    """
    try:
        result = await db.execute(
            """
            SELECT metric_key, grain, expression, source_priority, notes
            FROM meta_metric_definition
            """
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("schema_context: failed to read meta_metric_definition: %s", exc)
        return []
    return sorted(result.rows, key=lambda r: str(r.get("metric_key", "")))


async def _load_known_gaps(db: DuckDBSingleton) -> list[dict]:
    """Load unresolved + non-resolved gaps from `meta_known_gap`.

    Surface only `status NOT IN ('resolved')` to the agent.
    Sorted by `gap_key` for determinism.
    """
    try:
        result = await db.execute(
            """
            SELECT gap_key, severity, affected_area, status, details, recommended_action
            FROM meta_known_gap
            WHERE status <> 'resolved'
            ORDER BY gap_key
            """
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("schema_context: failed to read meta_known_gap: %s", exc)
        return []
    return list(result.rows)


async def _load_excluded_fate_tables(db: DuckDBSingleton) -> set[str]:
    """Return names of tables whose `meta_table_fate` row is excluded.

    Tables whose `meta_table_fate` row is `legacy_do_not_use`,
    `duplicate_superseded`, or `empty_endpoint_shell`. The agent sees
    these as caveats so it knows not to query them. Sorted set for
    determinism.
    """
    try:
        result = await db.execute(
            """
            SELECT original_table, fate
            FROM meta_table_fate
            WHERE fate = ANY($fates)
            """,
            {"fates": list(_EXCLUDED_FATES)},
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("schema_context: failed to read meta_table_fate: %s", exc)
        return set()
    return {str(row.get("original_table")) for row in result.rows if row.get("original_table")}


async def build_schema_context(db: DuckDBSingleton | None = None) -> SchemaContext:
    """Build a fresh `SchemaContext` from the warehouse.

    Parameters
    ----------
    db
        Optional `DuckDBSingleton`. Defaults to the process-wide
        `get_db()`. Tests may inject a different handle.

    All queries are SELECT-only against the warehouse; never DDL/DML.
    The function is idempotent and side-effect-free except for the
    read-only warehouse calls.
    """
    handle = db if db is not None else get_db()
    table_purposes, metric_definitions, known_gaps, table_fate_excluded = await asyncio.gather(
        _describe_tables(handle),
        _load_metric_definitions(handle),
        _load_known_gaps(handle),
        _load_excluded_fate_tables(handle),
    )
    return SchemaContext(
        table_purposes=table_purposes,
        metric_definitions=metric_definitions,
        known_gaps=known_gaps,
        table_fate_excluded=table_fate_excluded,
    )


_schema_context_cache: SchemaContext | None = None
_schema_context_lock = asyncio.Lock()


async def get_schema_context() -> SchemaContext:
    """Return the cached, process-wide `SchemaContext`.

    Built lazily on first call. The cache is a plain module-level
    variable guarded by an `asyncio.Lock` so concurrent first-callers
    don't race on the build. To rebuild (e.g. in tests after a
    warehouse swap), call `reset_schema_context_cache()`.
    """
    global _schema_context_cache
    if _schema_context_cache is not None:
        return _schema_context_cache
    async with _schema_context_lock:
        if _schema_context_cache is None:
            _schema_context_cache = await build_schema_context(get_db())
    return _schema_context_cache


def reset_schema_context_cache() -> None:
    """Clear the cached `SchemaContext` (test helper only)."""
    global _schema_context_cache
    _schema_context_cache = None


__all__ = [
    "ALLOWED_TABLES_FOR_AGENT",
    "SchemaContext",
    "build_schema_context",
    "get_schema_context",
    "reset_schema_context_cache",
]
