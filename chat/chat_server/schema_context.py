"""Compact trusted schema map for the Pydantic AI agent (PLAN §7.6).

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


#: Tables the agent is allowed to know about (PLAN §7.6). This is the
#: agent's VIEW, separate from per-template SQL allowlists. Tables in
#: `meta_table_fate` with fate `legacy_do_not_use`, `duplicate_superseded`,
#: or `empty_endpoint_shell` are deliberately omitted.
ALLOWED_TABLES_FOR_AGENT: frozenset[str] = frozenset(
    {
        # Canonical marts (9).
        "mart_player_season",
        "mart_player_career",
        "mart_draft_value",
        "mart_franchise_leaders",
        "mart_league_leaders",
        "mart_head_to_head",
        "mart_betting_summary",
        "mart_player_rolling",
        "mart_shot_zones",
        # Dimensions.
        "dim_player",
        "dim_team",
        "dim_team_era",
        "dim_game",
        "dim_official",
        "dim_arena",
        "dim_date",
        # Selected facts.
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
        # Source-backed (allowlisted only via source-backed templates).
        "src_bref_advanced",
        "src_bref_per_100_poss",
        "src_fact_bref_team_season_summary",
    }
)


#: Fate values that exclude a table from the agent's view (PLAN §7.6).
#: The agent sees their NAMES in the system prompt as a caveat, so it
#: knows they exist but must not query them.
_EXCLUDED_FATES: frozenset[str] = frozenset(
    {"legacy_do_not_use", "duplicate_superseded", "empty_endpoint_shell"}
)


#: Per-table one-line purpose hints. These are STATIC — the agent is told
#: "this is what this table is for" without hitting the warehouse. The
#: column list and grain come from `information_schema` at startup.
#: Keep entries short; the prompt is compact by design.
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
        "One row per (player, game). Box-score + is_win/is_home/opponent_team_id."
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
        "Grain: (team_id, season INT, is_playoffs BOOL). Source-backed."
    ),
}


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
        lines: list[str] = []
        lines.append("ALLOWED WAREHOUSE TABLES (only these tables may be queried):")
        if not self.table_purposes:
            lines.append("  (none discovered)")
        else:
            for name, purpose in self.table_purposes.items():
                lines.append(f"  - {name}: {purpose}")
        if self.metric_definitions:
            lines.append("")
            lines.append("METRIC DEFINITIONS (from meta_metric_definition):")
            for m in self.metric_definitions:
                metric_key = m.get("metric_key", "?")
                grain = m.get("grain") or "?"
                expr = m.get("expression") or ""
                notes = m.get("notes") or ""
                src = m.get("source_priority") or ""
                tail_bits: list[str] = []
                if expr:
                    tail_bits.append(f"expr={expr}")
                if src:
                    tail_bits.append(f"src={src}")
                if notes:
                    tail_bits.append(f"notes={notes}")
                tail = "; ".join(tail_bits)
                lines.append(f"  - {metric_key} [grain={grain}]{(' — ' + tail) if tail else ''}")
        if self.known_gaps:
            lines.append("")
            lines.append("KNOWN GAPS (caveats to surface in answers when relevant):")
            for g in self.known_gaps:
                gap_key = g.get("gap_key", "?")
                sev = g.get("severity") or "info"
                area = g.get("affected_area") or ""
                status = g.get("status") or ""
                details = g.get("details") or ""
                head = f"  - {gap_key} [{sev}, {status}, area={area}]"
                if details:
                    head += f" — {details}"
                lines.append(head)
        if self.table_fate_excluded:
            lines.append("")
            lines.append(
                "EXCLUDED TABLES (MUST NOT be queried — "
                "legacy/empty/duplicate; only mentioned when explaining warehouse state):"
            )
            for name in sorted(self.table_fate_excluded):
                lines.append(f"  - {name}")
        return "\n".join(lines)


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
    except Exception as exc:  # pragma: no cover  - defensive
        logger.warning("schema_context: failed to read information_schema.columns: %s", exc)
        return {}

    by_table: dict[str, list[str]] = {}
    for row in result.rows:
        name = row.get("table_name")
        col = row.get("column_name")
        if isinstance(name, str) and isinstance(col, str):
            by_table.setdefault(name, []).append(col)

    out: dict[str, str] = {}
    # Stable ordering: the allowlist is a frozenset, so sort it.
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
    """Load every row from `meta_metric_definition` (PLAN §7.6).

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
    except Exception as exc:  # pragma: no cover  - defensive
        logger.warning("schema_context: failed to read meta_metric_definition: %s", exc)
        return []
    # Sort for stable prompt output.
    return sorted(result.rows, key=lambda r: str(r.get("metric_key", "")))


async def _load_known_gaps(db: DuckDBSingleton) -> list[dict]:
    """Load unresolved + non-resolved gaps from `meta_known_gap`.

    PLAN §7.6: surface only `status NOT IN ('resolved')` to the agent.
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
    except Exception as exc:  # pragma: no cover  - defensive
        logger.warning("schema_context: failed to read meta_known_gap: %s", exc)
        return []
    return list(result.rows)


async def _load_excluded_fate_tables(db: DuckDBSingleton) -> set[str]:
    """Return names of tables whose `meta_table_fate` row is excluded.

    PLAN §7.6: `legacy_do_not_use`, `duplicate_superseded`,
    `empty_endpoint_shell`. The agent sees these as caveats so it knows
    not to query them. Sorted set for determinism.
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
    except Exception as exc:  # pragma: no cover  - defensive
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
    # Run the four independent reads concurrently — they touch separate
    # tables and the connection is read-only, so there's no contention
    # beyond what the DB pool already serializes.
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


# Module-level cache (cannot use `lru_cache` with async). The lock keeps
# concurrent first-callers from racing on the build.
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
