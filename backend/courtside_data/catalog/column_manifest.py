"""Column-semantic manifest for Phase 2+ Courtside Data endpoints.

The manifest is the *single source of truth* for "this API column lives
in that DB column, has that dtype/unit, is available from that season
onward." Every contract in ``ALL_COLUMN_CONTRACTS`` is checked against
the live DuckDB catalog by ``tests/schema/test_column_manifest_lineage.py``
so a stale declaration (renamed view, dropped column, schema drift)
fails the build at CI time — not at request time.

Why this exists
---------------
* The live DB has already surprised us: ``dim_player`` uses ``full_name``
  (not ``display_name``), ``fact_player_season_stats`` uses ``min``
  (not ``mp``), ``fact_team_season_summary`` uses ``w`` / ``l`` (not
  ``wins`` / ``losses``), and ``v_canonical_player_season_totals`` is
  uppercase ``PTS`` / ``AST`` / ``TRB`` / ``3P`` / ``3PA`` / ``3P%``.
* API keys should be the *public* (lowercase, snake_case) names the
  frontend speaks; lineage tuples are the *actual* column names the
  DuckDB catalog knows about. Decoupling the two means the API surface
  can stay stable while the warehouse is migrated.

Conventions
-----------
* ``key`` is the API-facing name. Frontend types in
  ``frontend/src/lib/openapi-types.ts`` should match this verbatim.
* ``lineage`` is ``(schema, table, column)`` — a tuple that *must*
  resolve to a real row in ``information_schema.columns`` for the
  build-time test to pass. Use real DB names, not spec names.
* ``available_since_season`` is the **ending calendar year** of the
  first season in which the column has meaningful data (e.g. 1980 for
  3P because the 1979-80 season introduced the 3-point line, 1978 for
  TOV because the 1977-78 season started tracking turnovers, 1974 for
  STL/BLK/OREB/DRB, 1984 for GS, 1947 for the rest).
* ``is_playoffs_scoped`` is True iff the column's value depends on
  whether the row is regular season or playoffs (i.e. the source
  table/view has an ``is_playoffs`` discriminator, or the view is
  a playoff-only view). For columns from regular-season-only views
  (``v_canonical_player_season_totals``) or tables that don't carry
  a playoff flag (``fact_team_season_summary``, ``dim_player``,
  ``dim_team``), this is False.

Columns deliberately omitted
----------------------------
* ``win_pct`` — not a real DB column; computed server-side as
  ``w / (w + l)``. No lineage to declare.
* ``height_inches`` / ``body_weight_lbs`` — DB has raw inches / lbs,
  but the API likely wants feet-and-inches / kg. A future
  ``unit_converter`` manifest is a better home for those.
* ``birth_date`` — the dtype Literal is
  ``int|float|decimal|str|bool`` and date doesn't fit cleanly.
* Career totals (``career_pts``, ``career_gp``, …) from
  ``api.v_player_career`` — Phase 2 endpoints will fetch via
  ``SUM(...)`` over ``v_canonical_player_season_totals``, so the
  same column contracts apply.
* Game boxscore columns beyond what ``golden.csv`` already pins
  (points, assists, blocks, steals, turnovers, fg3m, oreb, dreb, reb,
  fouls_personal) — the manifest stays focused on the column
  inventory the catalog + player/team endpoints need.

Adding new columns
------------------
1. Run ``duckdb -readonly data/nba.duckdb -c "SELECT column_name,
   data_type FROM information_schema.columns WHERE table_schema='…'
   AND table_name='…' ORDER BY ordinal_position"`` to confirm the
   real column name. Do not guess — DuckDB is case-sensitive.
2. Append a ``ColumnContract(...)`` to ``ALL_COLUMN_CONTRACTS``.
3. Re-run ``cd backend && uv run pytest tests/schema/
   test_column_manifest_lineage.py -v``. The lineage test will
   fail loudly if the tuple is wrong, missing, or mistyped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnLineage:
    """A single (schema, table, column) tuple that must exist in the live DB."""

    schema: str  # e.g. "api", "unified_star"
    table: str  # e.g. "v_canonical_player_season_totals", "dim_player"
    column: str  # the ACTUAL column name in the DB (case-sensitive!)


@dataclass(frozen=True)
class ColumnContract:
    """The public API contract for one column, with a DB lineage pointer."""

    key: str  # API-facing name; must match ColumnMeta.key on the wire
    label: str  # UI display label, e.g. "PTS", "FG%", "W"
    lineage: ColumnLineage
    canonical_metric_id: str | None  # FK into meta.canonical_metric when set
    dtype: Literal["int", "float", "decimal", "str", "bool"]
    unit: Literal[
        "points",
        "fraction",
        "percent",
        "decimal_minutes",
        "tenths_of_feet",
        "games",
        "count",
        "year",
        "slug",
    ]
    format_rule: str  # formatter name; see docstring above for the registry
    available_since_season: int  # ending calendar year (1947 = 1946-47)
    is_playoffs_scoped: bool  # True iff regular/playoff grain is meaningful


# ---------------------------------------------------------------------------
# Constants — column inventory
# ---------------------------------------------------------------------------

# Convention used everywhere below: ``available_since_season`` is the ENDING
# calendar year of the first NBA season in which the column has meaningful
# data. The league has run since the 1946-47 BAA season, so 1947 is the
# floor for traditional box-score stats.

# Format rules we currently recognise. Adding a new rule here should be
# accompanied by a formatter in `courtside_data.formatters` (TBD).
_FMT_INT = "int_no_format"
_FMT_DECIMAL1 = "decimal1"
_FMT_DECIMAL2 = "decimal2"
_FMT_PCT_FROM_FRACTION = "pct_from_fraction_x100"
_FMT_PCT_NO_FORMAT = "pct_no_format"  # already stored as 0-100
_FMT_MIN_TO_MMSS = "min_to_mmss"
_FMT_YEAR = "year"

ALL_COLUMN_CONTRACTS: list[ColumnContract] = [
    # -----------------------------------------------------------------
    # Player identity — unified_star.dim_player
    # -----------------------------------------------------------------
    ColumnContract(
        key="player_id",
        label="Player ID",
        lineage=ColumnLineage("unified_star", "dim_player", "player_id"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="bref_player_id",
        label="BBR player slug",
        lineage=ColumnLineage("unified_star", "dim_player", "bref_player_id"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="slug_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="json_slug",
        label="BBR JSON slug",
        lineage=ColumnLineage("unified_star", "dim_player", "json_slug"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="slug_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="first_name",
        label="First name",
        lineage=ColumnLineage("unified_star", "dim_player", "first_name"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="last_name",
        label="Last name",
        lineage=ColumnLineage("unified_star", "dim_player", "last_name"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="full_name",
        # Spec calls this `display_name`; the live DB uses `full_name`.
        # The build-time lineage test enforces the real name. See
        # `test_dim_player_has_spec_named_columns` xfail for the open
        # question of whether to rename the DB or the spec.
        label="Full name",
        lineage=ColumnLineage("unified_star", "dim_player", "full_name"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="is_active",
        label="Is active",
        lineage=ColumnLineage("unified_star", "dim_player", "is_active"),
        canonical_metric_id=None,
        dtype="bool",
        unit="count",
        format_rule="bool_yes_no",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="from_year",
        label="First season year",
        lineage=ColumnLineage("unified_star", "dim_player", "from_year"),
        canonical_metric_id=None,
        dtype="int",
        unit="year",
        format_rule=_FMT_YEAR,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="to_year",
        # `to_year` is NULL for currently active players; the API will
        # surface that as the most recent season rather than "9999".
        label="Last season year",
        lineage=ColumnLineage("unified_star", "dim_player", "to_year"),
        canonical_metric_id=None,
        dtype="int",
        unit="year",
        format_rule=_FMT_YEAR,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="draft_year",
        label="Draft year",
        lineage=ColumnLineage("unified_star", "dim_player", "draft_year"),
        canonical_metric_id=None,
        dtype="int",
        unit="year",
        format_rule=_FMT_YEAR,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    # -----------------------------------------------------------------
    # Traditional box score — api.v_canonical_player_season_totals
    # (regular-season-only canonical view; all columns are the
    # UPPERCASE bbref-style names that match the BBR web tables).
    # -----------------------------------------------------------------
    ColumnContract(
        key="pts",
        label="PTS",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "PTS"),
        canonical_metric_id="pts",
        dtype="int",
        unit="points",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="ast",
        label="AST",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "AST"),
        canonical_metric_id="ast",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="reb",
        # Canonical view calls it `TRB`; the season-stats table calls it
        # `reb`. We pick the canonical view (cleaner, regular-season-only)
        # and document the public name as `reb` because that's what
        # golden.csv's stat_key uses and what the API has historically
        # exposed. The pre-1973 caveat from the prior session: REB is
        # total rebounds (OREB + DREB), available since 1947.
        label="REB",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "TRB"),
        canonical_metric_id="reb",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="oreb",
        # First tracked in 1973-74. The DB stores pre-1974 as 0
        # (not NULL) per the Wilt 1962 NULL-vs-zero finding; callers
        # that need "missing data" semantics should filter by
        # `available_since_season` (>= 1974).
        label="OREB",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "ORB"),
        canonical_metric_id="oreb",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="dreb",
        # First tracked in 1973-74; pre-1974 stored as 0 in this view.
        label="DREB",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "DRB"),
        canonical_metric_id="dreb",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="stl",
        # First tracked in 1973-74; pre-1974 stored as 0 in this view.
        # `fact_player_season_stats.stl` IS NULL pre-1974 (verified
        # for Bill Russell / Wilt); the canonical view is zero-filled.
        label="STL",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "STL"),
        canonical_metric_id="stl",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="blk",
        # First tracked in 1973-74; pre-1974 stored as 0 in this view.
        label="BLK",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "BLK"),
        canonical_metric_id="blk",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="tov",
        # First tracked in 1977-78. Pre-1978 stored as 0 here (fact_
        # player_season_stats also zero-filled pre-1978).
        label="TOV",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "TOV"),
        canonical_metric_id="tov",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1978,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="pf",
        label="PF",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "PF"),
        canonical_metric_id="pf",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="gp",
        # Canonical view uses `G` (uppercase, integer). `fact_player_
        # season_stats` uses `gp`. We pick `G` from the canonical view
        # for consistency with the rest of the box-score inventory.
        label="GP",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "G"),
        canonical_metric_id="gp",
        dtype="int",
        unit="games",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="gs",
        # Games started — first tracked in 1983-84 (ending year 1984).
        # The canonical view has non-zero values back to 1971 because
        # the source BBR scrape back-fills from play-by-play; for
        # contract purposes we follow the league's official
        # introduction date.
        label="GS",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "GS"),
        canonical_metric_id="gs",
        dtype="int",
        unit="games",
        format_rule=_FMT_INT,
        available_since_season=1984,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="mp",
        # Canonical view stores MP as INTEGER (season total minutes).
        # `fact_player_season_stats` has the same data as lowercase
        # `min` (also INTEGER). Per-game view
        # `v_canonical_player_season_per_game` has MP as DOUBLE.
        # The public key `mp` follows the BBR convention; the lineage
        # points to the canonical season-total view.
        label="MP",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "MP"),
        canonical_metric_id="mp",
        dtype="int",
        unit="decimal_minutes",
        format_rule=_FMT_MIN_TO_MMSS,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fgm",
        # Canonical view calls it `FG`; the key `fgm` follows BBR.
        label="FGM",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "FG"),
        canonical_metric_id="fgm",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fga",
        label="FGA",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "FGA"),
        canonical_metric_id="fga",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fg_pct",
        # Stored as a 0-1 FRACTION in the canonical view (e.g. 0.450).
        # `fact_player_game_boxscore.fg_pct` is on the 0-100 scale with
        # extremes > 1 (e.g. 9.0 = 900% on 1-of-1 with a And-1) — the
        # two are not interchangeable; the API should pick one.
        label="FG%",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "FG%"),
        canonical_metric_id="fg_pct",
        dtype="float",
        unit="fraction",
        format_rule=_FMT_PCT_FROM_FRACTION,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="efg_pct",
        label="eFG%",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "eFG%"),
        canonical_metric_id="efg_pct",
        dtype="float",
        unit="fraction",
        format_rule=_FMT_PCT_FROM_FRACTION,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="ftm",
        # Canonical view calls it `FT`; the key `ftm` follows BBR.
        label="FTM",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "FT"),
        canonical_metric_id="ftm",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fta",
        label="FTA",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "FTA"),
        canonical_metric_id="fta",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="ft_pct",
        # Stored as a 0-1 fraction in the canonical view.
        label="FT%",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "FT%"),
        canonical_metric_id="ft_pct",
        dtype="float",
        unit="fraction",
        format_rule=_FMT_PCT_FROM_FRACTION,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fg2m",
        label="2P",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "2P"),
        canonical_metric_id="fg2m",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fg2a",
        label="2PA",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "2PA"),
        canonical_metric_id="fg2a",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fg2_pct",
        label="2P%",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "2P%"),
        canonical_metric_id="fg2_pct",
        dtype="float",
        unit="fraction",
        format_rule=_FMT_PCT_FROM_FRACTION,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fg3m",
        # 3-point line introduced in 1979-80 (ending year 1980). The
        # canonical view confirms 1980 as the first season with non-zero
        # 3P. The lineage column is literally `3P` (DuckDB identifiers
        # in this view are case-sensitive mixed-case like `2P`/`3P`).
        label="3P",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "3P"),
        canonical_metric_id="fg3m",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1980,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fg3a",
        label="3PA",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "3PA"),
        canonical_metric_id="fg3a",
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1980,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="fg3_pct",
        label="3P%",
        lineage=ColumnLineage("api", "v_canonical_player_season_totals", "3P%"),
        canonical_metric_id="fg3_pct",
        dtype="float",
        unit="fraction",
        format_rule=_FMT_PCT_FROM_FRACTION,
        available_since_season=1980,
        is_playoffs_scoped=False,
    ),
    # -----------------------------------------------------------------
    # Advanced box-score — unified_star.fact_player_season_stats
    # These columns are NOT exposed by the canonical view; they live
    # in the regular fact table. is_playoffs_scoped=True because
    # the source table carries an `is_playoffs` discriminator.
    # -----------------------------------------------------------------
    ColumnContract(
        key="per",
        # Player Efficiency Rating (BBR/Hollinger). Stored as DOUBLE.
        label="PER",
        lineage=ColumnLineage("unified_star", "fact_player_season_stats", "per"),
        canonical_metric_id="per",
        dtype="float",
        unit="count",
        format_rule=_FMT_DECIMAL1,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="ts_pct",
        # True Shooting Percentage. Stored as 0-1 fraction here; the
        # game boxscore has it on the 0-100 scale.
        label="TS%",
        lineage=ColumnLineage("unified_star", "fact_player_season_stats", "ts_pct"),
        canonical_metric_id="ts_pct",
        dtype="float",
        unit="fraction",
        format_rule=_FMT_PCT_FROM_FRACTION,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="bpm",
        # Box Plus/Minus (BBR). Signed float; can be negative.
        label="BPM",
        lineage=ColumnLineage("unified_star", "fact_player_season_stats", "bpm"),
        canonical_metric_id="bpm",
        dtype="float",
        unit="count",
        format_rule=_FMT_DECIMAL1,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="ows",
        label="OWS",
        lineage=ColumnLineage("unified_star", "fact_player_season_stats", "ows"),
        canonical_metric_id="ows",
        dtype="float",
        unit="count",
        format_rule=_FMT_DECIMAL1,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="dws",
        label="DWS",
        lineage=ColumnLineage("unified_star", "fact_player_season_stats", "dws"),
        canonical_metric_id="dws",
        dtype="float",
        unit="count",
        format_rule=_FMT_DECIMAL1,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="vorp",
        label="VORP",
        lineage=ColumnLineage("unified_star", "fact_player_season_stats", "vorp"),
        canonical_metric_id="vorp",
        dtype="float",
        unit="count",
        format_rule=_FMT_DECIMAL1,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="usg_pct",
        # Usage percentage. 0-1 fraction in the season-stats table;
        # the game boxscore has it on the 0-100 scale.
        label="USG%",
        lineage=ColumnLineage("unified_star", "fact_player_season_stats", "usg_pct"),
        canonical_metric_id="usg_pct",
        dtype="float",
        unit="fraction",
        format_rule=_FMT_PCT_FROM_FRACTION,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    # -----------------------------------------------------------------
    # Team season — unified_star.fact_team_season_summary
    # DB columns are lowercase `w` / `l`; the API uses the public
    # `wins` / `losses` keys.
    # -----------------------------------------------------------------
    ColumnContract(
        key="wins",
        label="W",
        lineage=ColumnLineage("unified_star", "fact_team_season_summary", "w"),
        canonical_metric_id="wins",
        dtype="int",
        unit="games",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="losses",
        label="L",
        lineage=ColumnLineage("unified_star", "fact_team_season_summary", "l"),
        canonical_metric_id="losses",
        dtype="int",
        unit="games",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="o_rtg",
        label="ORtg",
        lineage=ColumnLineage("unified_star", "fact_team_season_summary", "o_rtg"),
        canonical_metric_id="o_rtg",
        dtype="float",
        unit="count",
        format_rule=_FMT_DECIMAL1,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="d_rtg",
        label="DRtg",
        lineage=ColumnLineage("unified_star", "fact_team_season_summary", "d_rtg"),
        canonical_metric_id="d_rtg",
        dtype="float",
        unit="count",
        format_rule=_FMT_DECIMAL1,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    # -----------------------------------------------------------------
    # Team identity — unified_star.dim_team
    # Spec says `full_name`; the live DB uses `team_abbrev` + `team_
    # name` + `team_city` instead. We declare the live columns.
    # -----------------------------------------------------------------
    ColumnContract(
        key="team_id",
        label="Team ID",
        lineage=ColumnLineage("unified_star", "dim_team", "team_id"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="team_abbrev",
        # The current short abbreviation, e.g. "BOS", "GSW". Historical
        # names live in separate dim_team rows scoped by
        # `season_founded` / `season_active_till`.
        label="Abbrev",
        lineage=ColumnLineage("unified_star", "dim_team", "team_abbrev"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="team_city",
        label="City",
        lineage=ColumnLineage("unified_star", "dim_team", "team_city"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="team_name",
        label="Team name",
        lineage=ColumnLineage("unified_star", "dim_team", "team_name"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="bref_team_code",
        # BBR-side team code. Same characters as `team_abbrev` in most
        # cases; differs for franchises that have used multiple codes
        # (e.g. GSW = "PHI" 1946-61, "SF" 1962-70, "GSW" 1971+).
        label="BBR team code",
        lineage=ColumnLineage("unified_star", "dim_team", "bref_team_code"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="league",
        label="League",
        lineage=ColumnLineage("unified_star", "dim_team", "league"),
        canonical_metric_id=None,
        dtype="str",
        unit="slug",
        format_rule="str_no_format",
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="season_founded",
        label="Founded",
        lineage=ColumnLineage("unified_star", "dim_team", "season_founded"),
        canonical_metric_id=None,
        dtype="int",
        unit="year",
        format_rule=_FMT_YEAR,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    ColumnContract(
        key="season_active_till",
        label="Active till",
        lineage=ColumnLineage("unified_star", "dim_team", "season_active_till"),
        canonical_metric_id=None,
        dtype="int",
        unit="year",
        format_rule=_FMT_YEAR,
        available_since_season=1947,
        is_playoffs_scoped=False,
    ),
    # -----------------------------------------------------------------
    # Per-game box score — unified_star.fact_player_game_boxscore
    # Each row is a single game; is_playoffs_scoped=True because
    # each game is either regular season or playoff. The percentages
    # here are on the 0-100 scale (NOT fractions), so the format
    # rule is `pct_no_format` rather than `pct_from_fraction_x100`.
    # -----------------------------------------------------------------
    ColumnContract(
        key="game_points",
        label="Game PTS",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "points"),
        canonical_metric_id=None,
        dtype="int",
        unit="points",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_assists",
        label="Game AST",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "assists"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_blocks",
        label="Game BLK",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "blocks"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_steals",
        label="Game STL",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "steals"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_turnovers",
        label="Game TOV",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "turnovers"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1978,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_oreb",
        label="Game OREB",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "oreb"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_dreb",
        label="Game DREB",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "dreb"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1974,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_reb",
        label="Game REB",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "reb"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_fouls_personal",
        label="Game PF",
        lineage=ColumnLineage(
            "unified_star", "fact_player_game_boxscore", "fouls_personal"
        ),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1947,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_fg3m",
        label="Game 3P",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "fg3m"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1980,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_fg3a",
        label="Game 3PA",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "fg3a"),
        canonical_metric_id=None,
        dtype="int",
        unit="count",
        format_rule=_FMT_INT,
        available_since_season=1980,
        is_playoffs_scoped=True,
    ),
    ColumnContract(
        key="game_fg3_pct",
        # Per-game box score stores percentages on the 0-100 scale
        # (with extremes like 9.0 for 1-of-1 + And-1). NOT a fraction.
        label="Game 3P%",
        lineage=ColumnLineage("unified_star", "fact_player_game_boxscore", "fg3_pct"),
        canonical_metric_id=None,
        dtype="float",
        unit="percent",
        format_rule=_FMT_PCT_NO_FORMAT,
        available_since_season=1980,
        is_playoffs_scoped=True,
    ),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


_BY_KEY: dict[str, ColumnContract] = {c.key: c for c in ALL_COLUMN_CONTRACTS}


def by_key(key: str) -> ColumnContract:
    """Return the contract for ``key`` or raise ``KeyError`` if missing.

    Used by the OpenAPI generator to validate that every catalog metric
    the frontend asks for has a declared lineage.
    """
    return _BY_KEY[key]


# Sentinel: when the manifest was last regenerated. The
# `test_manifest_has_recent_generated_at` schema test fails (with a
# soft message) if this is more than 180 days old, prompting a
# maintainer to re-walk the live DuckDB schema.
__generated_at__ = "2026-06-29"


__all__ = [
    "ALL_COLUMN_CONTRACTS",
    "ColumnContract",
    "ColumnLineage",
    "by_key",
]
