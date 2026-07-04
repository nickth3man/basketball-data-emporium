#!/usr/bin/env python
"""Build a fresh DuckDB-only NBA warehouse.

This is a local rebuild utility for the data-quality workstream. It creates a
new database from an existing raw warehouse plus local staged/audit/anchor
files:

    python data/audit/build_nba.py --source-db /path/to/raw.duckdb --replace

The default materializes lossless `src_*` copies of every base table in the
source database, then builds normalized `map_*`, canonical `dim_*`/`fact_*`,
and derived `mart_*` tables. For fast SQL validation without copying the large
source layer, use:

    python data/audit/build_nba.py --source-db /path/to/raw.duckdb --replace --source-mode view --skip-source-hashes

The view mode is a smoke-test mode only; use the default copy mode for the
portable database artifact.

Note: data/nba.duckdb is now itself a built artifact of this script (the raw
warehouse it was originally built from has been archived outside this repo).
There is no default --source-db for that reason -- pass the archived raw
warehouse path explicitly if you need to rebuild from scratch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[2]
# No default source: data/nba.duckdb is itself a built artifact of this
# script now (the original raw warehouse it was built from has been archived
# outside this repo) -- pass --source-db explicitly to rebuild from scratch.
DEFAULT_SOURCE_DB = None
DEFAULT_TARGET_DB = REPO_ROOT / "data" / "nba.duckdb"
ANCHOR_FILES = {
    "src_anchor_bbr_jerseys": REPO_ROOT / "data" / "anchors" / "bbr_jerseys.jsonl",
    "src_anchor_bbr_coaches": REPO_ROOT / "data" / "anchors" / "bbr_coaches.jsonl",
}
AUDIT_OUT_DIR = REPO_ROOT / "data" / "audit" / "out"


SOURCE_PRIORITY = {
    "games": "fact_game, then Kaggle staged games, then schedule/placeholders",
    "player_season": "BBR-resolved season facts plus BBR crosswalk fallback",
    "awards": "stg_bref award tables rebuilt into fact_player_awards",
    "standings": "fact_standings after BBR W/L repair",
    "jerseys": "inactive lists, then BBR anchors, then inferred/bridge fallback",
    "pbp": "fact_pbp_events / Kaggle staged PBP coverage",
    "shots": "fact_shot_chart row-level attempts",
    "odds": "fact_game_betting_lines and market-odds snapshots",
}


CANONICAL_SOURCE_TABLES = {
    "dim_all_players",
    "dim_bref_player",
    "dim_date",
    "dim_defunct_team",
    "dim_game",
    "dim_official",
    "dim_player",
    "dim_team",
    "dim_team_history",
    "dim_team_season",
    "bridge_game_market_odds",
    "bridge_game_source_id",
    "bridge_game_team",
    "bridge_lineup_player",
    "bridge_play_player",
    "bridge_player_bbr",
    "bridge_player_source_id",
    "bridge_player_team_season",
    "bridge_team_bbr",
    "bridge_team_source_id",
    "draft_history",
    "fact_box_score_team",
    "fact_coach_season",
    "fact_game",
    "fact_game_betting_lines",
    "fact_game_market_odds",
    "fact_game_official",
    "fact_game_quarter_scores",
    "fact_pbp_events",
    "fact_player_awards",
    "fact_player_game_advanced",
    "fact_player_game_boxscore",
    "fact_player_jersey_season",
    "fact_player_season_stat_resolved",
    "fact_shot_chart",
    "fact_standings",
    "fact_starting_lineup_player",
}


DERIVED_PREFIXES = ("agg_", "analytics_")
SOURCE_PREFIXES = ("stg_", "_staging_chunks__")
LEGACY_MARKERS = ("_legacy_", "_pre_kaggle_backfill", "legacy_nba_api")
SUPERSEDED_TABLES = {
    "game",
    "line_score",
    "officials",
    "play_by_play",
    "fact_play_by_play",
    "fact_play_by_play_v2",
    "fact_play_by_play_v3",
    "fact_play_by_play_legacy_nba_api",
    "fact_scoreboard_line_score",
}


@dataclass
class SourceTable:
    name: str
    table_type: str
    row_count: int
    column_count: int
    source_copy_name: str
    table_class: str
    source_system: str


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def safe_name(prefix: str, name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return f"{prefix}{cleaned}"


def table_class(table_name: str) -> str:
    if table_name.startswith("stg_") or table_name.startswith("_staging_chunks__"):
        return "staging"
    if table_name.startswith("fact_"):
        return "fact"
    if table_name.startswith("dim_"):
        return "dimension"
    if table_name.startswith("bridge_"):
        return "bridge"
    if table_name.startswith("agg_"):
        return "aggregate"
    if table_name.startswith("analytics_"):
        return "analytics"
    if table_name.startswith("meta_") or table_name.startswith("_pipeline_"):
        return "metadata"
    return "other"


def infer_source_system(table_name: str) -> str:
    if table_name.startswith("stg_kaggle_nba_"):
        return "kaggle_nba"
    if table_name.startswith("stg_espn_nba_"):
        return "espn_nba"
    if table_name.startswith("stg_bref_") or table_name.startswith("fact_bref_"):
        return "basketball_reference"
    if table_name in {"bridge_player_bbr", "bridge_team_bbr"}:
        return "basketball_reference_crosswalk"
    if table_name in {"fact_coach_season", "fact_player_jersey_season"}:
        return "basketball_reference_anchor_materialized"
    if table_name.startswith("bridge_"):
        return "warehouse_bridge"
    if table_name.startswith(("dim_", "fact_", "agg_", "analytics_")):
        return "warehouse_v1"
    return "warehouse_source"


def read_app_table_references() -> dict[str, int]:
    queries = REPO_ROOT / "web" / "server" / "queries.ts"
    if not queries.exists():
        return {}
    text = queries.read_text(encoding="utf-8", errors="ignore")
    refs: dict[str, int] = {}
    for match in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.IGNORECASE):
        table = match.group(1)
        refs[table] = refs.get(table, 0) + 1
    return refs


def classify_fate(table: SourceTable, app_refs: dict[str, int]) -> tuple[str, str, str | None]:
    name = table.name
    if table.row_count == 0:
        return "empty_endpoint_shell", "0 rows in current warehouse; keep only as declared upstream gap", None
    if any(marker in name for marker in LEGACY_MARKERS):
        return "legacy_do_not_use", "forensic copy of a repaired or superseded table", None
    if name in SUPERSEDED_TABLES:
        return "duplicate_superseded", "superseded by a canonical table with better coverage or side semantics", None
    if name in CANONICAL_SOURCE_TABLES or name.startswith(("stg_kaggle_nba_", "stg_bref_")):
        return "canonical_source", "trusted as an input to canonical dimensions/facts", None
    if name.startswith(SOURCE_PREFIXES):
        return "lossless_source_only", "raw/staged input retained for provenance and future transforms", None
    if name.startswith(DERIVED_PREFIXES):
        return "derived_rebuild", "aggregate/analytics output must be rebuilt from canonical facts", None
    if app_refs.get(name, 0):
        return "app_only_compat_candidate", "referenced by the current app but not promoted as a canonical source", None
    return "lossless_source_only", "retained in source layer; not selected for the canonical model", None


class WarehouseBuilder:
    def __init__(
        self,
        source_db: Path,
        target_db: Path,
        source_mode: str,
        source_hashes: bool,
        verbose: bool,
    ) -> None:
        self.source_db = source_db
        self.target_db = target_db
        self.source_mode = source_mode
        self.source_hashes = source_hashes
        self.verbose = verbose
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("nba_build_%Y%m%dT%H%M%SZ")
        self.app_refs = read_app_table_references()
        self.source_tables: list[SourceTable] = []
        self.con: duckdb.DuckDBPyConnection | None = None

    @property
    def c(self) -> duckdb.DuckDBPyConnection:
        if self.con is None:
            raise RuntimeError("connection not open")
        return self.con

    def log(self, message: str) -> None:
        print(message, flush=True)

    def execute(self, sql: str, params: Iterable[object] | None = None) -> None:
        if self.verbose:
            compact = " ".join(sql.strip().split())
            self.log(f"SQL {compact[:220]}")
        self.c.execute(sql, params or [])

    def scalar(self, sql: str, params: Iterable[object] | None = None) -> object:
        return self.c.execute(sql, params or []).fetchone()[0]

    def has_source_table(self, table: str) -> bool:
        return any(t.name == table for t in self.source_tables)

    def src(self, table: str) -> str:
        if not self.has_source_table(table):
            raise KeyError(f"source table missing: {table}")
        return q(safe_name("src_", table))

    def connect(self) -> None:
        self.con = duckdb.connect(str(self.target_db))
        self.execute("SET threads TO 8")
        self.execute("SET preserve_insertion_order TO false")
        self.execute(f"ATTACH {sql_literal(str(self.source_db).replace('\\', '/'))} AS src (READ_ONLY)")

    def load_source_catalog(self) -> None:
        self.log("Cataloging source tables with exact row counts")
        src_con = duckdb.connect(str(self.source_db), read_only=True)
        rows = src_con.execute(
            """
            SELECT t.table_name, t.table_type,
                   count(c.column_name) AS column_count
            FROM information_schema.tables t
            LEFT JOIN information_schema.columns c
              ON c.table_schema = t.table_schema AND c.table_name = t.table_name
            WHERE t.table_schema = 'main'
              AND t.table_type = 'BASE TABLE'
            GROUP BY 1, 2
            ORDER BY 1
            """
        ).fetchall()
        self.source_tables = []
        for i, (name, table_type, column_count) in enumerate(rows, start=1):
            table_name = str(name)
            row_count = int(src_con.execute(f"SELECT count(*) FROM {q(table_name)}").fetchone()[0])
            self.source_tables.append(
                SourceTable(
                    name=table_name,
                    table_type=str(table_type),
                    row_count=row_count,
                    column_count=int(column_count or 0),
                    source_copy_name=safe_name("src_", table_name),
                    table_class=table_class(table_name),
                    source_system=infer_source_system(table_name),
                )
            )
            if self.verbose or i % 50 == 0 or i == len(rows):
                self.log(f"  catalog {i:>3}/{len(rows)} {table_name:<58} {row_count:>12,} rows")
        src_con.close()

    def create_metadata_tables(self) -> None:
        self.log("Creating metadata tables")
        self.execute(
            """
            CREATE OR REPLACE TABLE meta_build_run (
              build_run_id VARCHAR PRIMARY KEY,
              source_db_path VARCHAR,
              target_db_path VARCHAR,
              source_mode VARCHAR,
              source_hashes BOOLEAN,
              started_at TIMESTAMP,
              completed_at TIMESTAMP,
              status VARCHAR,
              notes VARCHAR
            )
            """
        )
        self.execute(
            """
            INSERT INTO meta_build_run
            VALUES (?, ?, ?, ?, ?, current_timestamp, NULL, 'running', ?)
            """,
            [
                self.run_id,
                str(self.source_db),
                str(self.target_db),
                self.source_mode,
                self.source_hashes,
                "Fresh DuckDB warehouse from current local warehouse plus staged/local files.",
            ],
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE meta_source_table (
              build_run_id VARCHAR,
              original_table VARCHAR,
              source_table VARCHAR,
              table_type VARCHAR,
              table_class VARCHAR,
              source_system VARCHAR,
              original_row_count BIGINT,
              source_row_count BIGINT,
              column_count BIGINT,
              source_row_hash_xor UBIGINT,
              materialization VARCHAR,
              loaded_at TIMESTAMP
            )
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE meta_table_fate (
              original_table VARCHAR PRIMARY KEY,
              source_table VARCHAR,
              table_class VARCHAR,
              fate VARCHAR,
              reason VARCHAR,
              original_row_count BIGINT,
              app_reference_count BIGINT,
              canonical_target VARCHAR
            )
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE meta_column_lineage (
              target_table VARCHAR,
              target_column VARCHAR,
              source_table VARCHAR,
              source_column VARCHAR,
              transform_note VARCHAR
            )
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE meta_quality_check (
              build_run_id VARCHAR,
              check_group VARCHAR,
              check_name VARCHAR,
              status VARCHAR,
              severity VARCHAR,
              observed_value VARCHAR,
              expected_value VARCHAR,
              details VARCHAR,
              checked_at TIMESTAMP
            )
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE meta_metric_definition (
              metric_key VARCHAR PRIMARY KEY,
              grain VARCHAR,
              expression VARCHAR,
              source_priority VARCHAR,
              notes VARCHAR
            )
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE meta_known_gap (
              gap_key VARCHAR PRIMARY KEY,
              severity VARCHAR,
              affected_area VARCHAR,
              status VARCHAR,
              details VARCHAR,
              recommended_action VARCHAR
            )
            """
        )
        self.insert_static_metadata()

    def insert_static_metadata(self) -> None:
        metric_rows = [
            ("pts", "player_game/team_game/player_season", "points scored", SOURCE_PRIORITY["player_season"], "Use source-specific column names only in src_* tables."),
            ("reb", "player_game/player_season", "total rebounds", SOURCE_PRIORITY["player_season"], "BBR pre-1974 seasons may carry TRB without ORB/DRB split."),
            ("ast", "player_game/player_season", "assists", SOURCE_PRIORITY["player_season"], ""),
            ("win_pct", "team_season", "wins / NULLIF(wins + losses, 0)", SOURCE_PRIORITY["standings"], "Regular-season standings exclude play-in games."),
            ("ts_pct", "player_season", "pts / (2 * (fga + 0.44 * fta))", SOURCE_PRIORITY["player_season"], "BBR/NBA calculations may differ slightly by source."),
            ("source_record_hash", "src_row", "hash(all original columns)", "source layer", "Used for row-level provenance, not a stable public id."),
        ]
        self.c.executemany(
            "INSERT INTO meta_metric_definition VALUES (?, ?, ?, ?, ?)",
            metric_rows,
        )
        gap_rows = [
            (
                "empty_endpoint_shells",
                "warn",
                "NBA.com endpoint passthrough tables",
                "documented",
                "The v1 warehouse includes many 0-row endpoint shells. They are source-layer declarations, not canonical facts.",
                "Keep as source-only until a real upstream pull populates them.",
            ),
            (
                "non_nba_kaggle_teams",
                "info",
                "team source mapping",
                "expected",
                "Kaggle contains synthetic/non-NBA exhibition team ids that cannot resolve to NBA franchises.",
                "Preserve unresolved map rows with reason instead of inventing teams.",
            ),
            (
                "player_source_unresolved_reason_backfill",
                "info",
                "player source mapping",
                "resolved_in_v1",
                "v1 bridge_player_source_id lacked unresolved_reason while game/team bridges had it; "
                "data/ingest/ingest.py now backfills the column and populates it (generic exact-id-match "
                "miss, plus a kaggle_nba-specific classifier distinguishing officials/staff from genuine "
                "non-NBA exhibition-club players). map_player_source_id passes it through directly.",
                "None -- fixed at the source. Re-run data/ingest/ingest.py's --reconcile-bbr and "
                "kaggle_nba --resolve-only, then rebuild, if this table ever looks stale again.",
            ),
            (
                "bbr_bridge_residual_unresolved_players",
                "info",
                "player source mapping",
                "backlog",
                "After reconciling bridge_player_source_id's basketball_reference/json_slug rows against "
                "bridge_player_bbr (data/ingest/ingest.py --reconcile-bbr), 401 ids still have no "
                "candidate row in bridge_player_bbr at all -- mostly obscure/short-career historical "
                "players (sample: sobekch01, johnsra01, bialowe01, hadnoji01, kramest01). This is a "
                "genuine bridge_player_bbr coverage gap, not a reconciliation bug.",
                "Needs new BBR crosswalk coverage (matching-logic work), not a quick fix; "
                "see the bbr_residual_gap_within_ceiling quality check for the current count.",
            ),
            (
                "app_contract_not_preserved",
                "info",
                "web app",
                "intentional",
                "This build does not preserve current Express query table names.",
                "Migrate app queries later or add compatibility views if needed.",
            ),
        ]
        self.c.executemany("INSERT INTO meta_known_gap VALUES (?, ?, ?, ?, ?, ?)", gap_rows)

    def source_columns(self, table_name: str) -> list[str]:
        rows = self.c.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_catalog = 'src' AND table_schema = 'main' AND table_name = ?
            ORDER BY ordinal_position
            """,
            [table_name],
        ).fetchall()
        return [str(r[0]) for r in rows]

    def create_source_layer(self) -> None:
        self.log(f"Creating source layer in {self.source_mode!r} mode for {len(self.source_tables)} base tables")
        for i, table in enumerate(self.source_tables, start=1):
            start = time.time()
            cols = self.source_columns(table.name)
            hash_expr = "hash(" + ", ".join(q(c) for c in cols) + ")" if cols else "hash('no_columns')"
            normalized_game = self.normalized_game_expr(cols)
            select_cols = [
                f"{sql_literal(self.run_id)} AS _ingest_run_id",
                f"{sql_literal(table.source_system)} AS _source_system",
                f"{sql_literal(table.name)} AS _source_table",
                f"CAST({hash_expr} AS UBIGINT) AS _source_record_hash",
                f"{normalized_game} AS _normalized_game_id",
                "*",
            ]
            source_relation = f"src.main.{q(table.name)}"
            target_relation = q(table.source_copy_name)
            verb = "VIEW" if self.source_mode == "view" else "TABLE"
            self.execute(
                f"CREATE OR REPLACE {verb} {target_relation} AS "
                f"SELECT {', '.join(select_cols)} FROM {source_relation}"
            )
            actual_rows = table.row_count if self.source_mode == "view" else int(self.scalar(f"SELECT count(*) FROM {target_relation}"))
            row_hash = None
            if self.source_hashes:
                row_hash = self.scalar(f"SELECT coalesce(bit_xor(_source_record_hash), 0::UBIGINT) FROM {target_relation}")
            self.execute(
                """
                INSERT INTO meta_source_table
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                """,
                [
                    self.run_id,
                    table.name,
                    table.source_copy_name,
                    table.table_type,
                    table.table_class,
                    table.source_system,
                    table.row_count,
                    actual_rows,
                    table.column_count,
                    row_hash,
                    self.source_mode,
                ],
            )
            if self.verbose or i % 25 == 0 or i == len(self.source_tables):
                self.log(
                    f"  {i:>3}/{len(self.source_tables)} {table.source_copy_name:<58} "
                    f"{actual_rows:>12,} rows ({time.time() - start:.1f}s)"
                )
        self.create_file_sources()

    def normalized_game_expr(self, columns: list[str]) -> str:
        lower = {c.lower(): c for c in columns}
        if "game_id" in lower:
            return f"lpad(trim(CAST({q(lower['game_id'])} AS VARCHAR)), 10, '0')"
        if "gameid" in lower:
            return f"lpad(trim(CAST({q(lower['gameid'])} AS VARCHAR)), 10, '0')"
        if "game_id_nullable" in lower:
            return f"lpad(trim(CAST({q(lower['game_id_nullable'])} AS VARCHAR)), 10, '0')"
        return "CAST(NULL AS VARCHAR)"

    def create_file_sources(self) -> None:
        for table_name, path in ANCHOR_FILES.items():
            if path.exists():
                self.create_file_source(table_name, path, "basketball_reference_anchor")
        if AUDIT_OUT_DIR.exists():
            for path in sorted(AUDIT_OUT_DIR.iterdir()):
                if path.suffix.lower() not in {".csv", ".parquet"}:
                    continue
                self.create_file_source(safe_name("src_audit_out_", path.stem), path, "audit_output")

    def create_file_source(self, table_name: str, path: Path, source_system: str) -> None:
        rel_path = str(path).replace("\\", "/")
        reader = f"read_parquet({sql_literal(rel_path)})" if path.suffix.lower() == ".parquet" else (
            f"read_json_auto({sql_literal(rel_path)})" if path.suffix.lower() == ".jsonl" else f"read_csv_auto({sql_literal(rel_path)})"
        )
        self.execute(f"CREATE OR REPLACE TEMP TABLE _file_source AS SELECT * FROM {reader}")
        cols = [r[0] for r in self.c.execute("DESCRIBE _file_source").fetchall()]
        hash_expr = "hash(" + ", ".join(q(c) for c in cols) + ")" if cols else "hash('no_columns')"
        normalized_game = self.normalized_game_expr(cols)
        verb = "VIEW" if self.source_mode == "view" and path.suffix.lower() != ".jsonl" else "TABLE"
        # Local file views would be path-dependent and fragile, so JSONL anchors
        # are always materialized even in smoke mode.
        if verb == "VIEW":
            self.execute(
                f"CREATE OR REPLACE VIEW {q(table_name)} AS SELECT "
                f"{sql_literal(self.run_id)} AS _ingest_run_id, "
                f"{sql_literal(source_system)} AS _source_system, "
                f"{sql_literal(path.name)} AS _source_table, "
                f"CAST({hash_expr} AS UBIGINT) AS _source_record_hash, "
                f"{normalized_game} AS _normalized_game_id, * FROM {reader}"
            )
        else:
            self.execute(
                f"CREATE OR REPLACE TABLE {q(table_name)} AS SELECT "
                f"{sql_literal(self.run_id)} AS _ingest_run_id, "
                f"{sql_literal(source_system)} AS _source_system, "
                f"{sql_literal(path.name)} AS _source_table, "
                f"CAST({hash_expr} AS UBIGINT) AS _source_record_hash, "
                f"{normalized_game} AS _normalized_game_id, * FROM _file_source"
            )
        count = int(self.scalar(f"SELECT count(*) FROM {q(table_name)}"))
        row_hash = None
        if self.source_hashes:
            row_hash = self.scalar(f"SELECT coalesce(bit_xor(_source_record_hash), 0::UBIGINT) FROM {q(table_name)}")
        self.execute(
            """
            INSERT INTO meta_source_table
            VALUES (?, ?, ?, 'BASE TABLE', 'file_source', ?, ?, ?, ?, ?, ?, current_timestamp)
            """,
            [self.run_id, path.name, table_name, source_system, count, count, len(cols), row_hash, verb.lower()],
        )

    def populate_table_fates(self) -> None:
        self.log("Classifying source table fates")
        rows = []
        for table in self.source_tables:
            fate, reason, target = classify_fate(table, self.app_refs)
            rows.append(
                (
                    table.name,
                    table.source_copy_name,
                    table.table_class,
                    fate,
                    reason,
                    table.row_count,
                    self.app_refs.get(table.name, 0),
                    target,
                )
            )
        self.c.executemany("INSERT INTO meta_table_fate VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def create_macros(self) -> None:
        self.execute(
            """
            CREATE OR REPLACE MACRO end_year_to_season(y) AS
              CAST(y - 1 AS VARCHAR) || '-' || lpad(CAST(y % 100 AS VARCHAR), 2, '0')
            """
        )
        self.execute(
            """
            CREATE OR REPLACE MACRO season_start_year(season_year) AS
              TRY_CAST(substr(CAST(season_year AS VARCHAR), 1, 4) AS INTEGER)
            """
        )

    def build_maps(self) -> None:
        self.log("Building standardized identity maps")
        self.execute(
            f"""
            CREATE OR REPLACE TABLE map_player_source_id AS
            SELECT
              'player' AS entity_type,
              source_system,
              source_player_id AS source_id,
              person_id AS player_id,
              CASE
                WHEN coalesce(is_unresolved, false) THEN 'unresolved'
                WHEN coalesce(is_ambiguous, false) THEN 'ambiguous'
                ELSE 'resolved'
              END AS resolution_status,
              unresolved_reason,
              match_method,
              match_confidence AS confidence,
              coalesce(is_ambiguous, false) AS is_ambiguous,
              json_object('source_system', source_system, 'source_player_id', source_player_id, 'player_id', person_id) AS evidence_json
            FROM {self.src('bridge_player_source_id')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE map_team_source_id AS
            SELECT
              'team' AS entity_type,
              source_system,
              source_team_id AS source_id,
              source_team_name,
              team_id,
              CASE
                WHEN coalesce(is_unresolved, false) THEN 'unresolved'
                WHEN coalesce(is_ambiguous, false) THEN 'ambiguous'
                ELSE 'resolved'
              END AS resolution_status,
              unresolved_reason,
              match_method,
              match_confidence AS confidence,
              coalesce(is_ambiguous, false) AS is_ambiguous,
              json_object('source_system', source_system, 'source_team_id', source_team_id, 'source_team_name', source_team_name, 'team_id', team_id) AS evidence_json
            FROM {self.src('bridge_team_source_id')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE map_game_source_id AS
            SELECT
              'game' AS entity_type,
              source_system,
              source_game_id AS source_id,
              game_id,
              CASE
                WHEN coalesce(is_unresolved, false) THEN 'unresolved'
                WHEN coalesce(is_ambiguous, false) THEN 'ambiguous'
                ELSE 'resolved'
              END AS resolution_status,
              unresolved_reason,
              match_method,
              match_confidence AS confidence,
              coalesce(is_ambiguous, false) AS is_ambiguous,
              json_object('source_system', source_system, 'source_game_id', source_game_id, 'game_id', game_id) AS evidence_json
            FROM {self.src('bridge_game_source_id')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE map_player_bbr AS
            WITH ranked AS (
              SELECT b.*,
                     row_number() OVER (
                       PARTITION BY bbr_player_id
                       ORDER BY coalesce(g.gp, 0) DESC, nba_player_id
                     ) AS preferred_rank
              FROM {self.src('bridge_player_bbr')} b
              LEFT JOIN (
                SELECT player_id, count(*) AS gp
                FROM {self.src('fact_player_game_boxscore')}
                GROUP BY 1
              ) g ON g.player_id = b.nba_player_id
            )
            SELECT
              bbr_player_id,
              nba_player_id AS player_id,
              full_name,
              method AS match_method,
              span_score,
              preferred_rank = 1 AS is_preferred,
              CASE WHEN preferred_rank = 1 THEN 'resolved' ELSE 'duplicate_identity_alias' END AS resolution_status,
              json_object('bbr_player_id', bbr_player_id, 'nba_player_id', nba_player_id, 'method', method, 'span_score', span_score) AS evidence_json
            FROM ranked
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE map_team_bbr AS
            SELECT
              season,
              team_id,
              team_abbreviation,
              bbr_abbreviation,
              bbr_team_name,
              lg,
              'resolved' AS resolution_status,
              json_object('season', season, 'team_id', team_id, 'bbr_abbreviation', bbr_abbreviation, 'lg', lg) AS evidence_json
            FROM {self.src('bridge_team_bbr')}
            """
        )
        if self.has_source_table("bridge_game_team"):
            self.execute(
                f"""
                CREATE OR REPLACE TABLE map_game_team AS
                SELECT game_id, team_id, side, wl, season_year,
                       CASE WHEN side IN ('home', 'away') THEN 'resolved' ELSE 'unknown_side' END AS resolution_status
                FROM {self.src('bridge_game_team')}
                """
            )
        else:
            self.execute(
                """
                CREATE OR REPLACE TABLE map_game_team AS
                SELECT game_id, home_team_id AS team_id, 'home' AS side,
                       CASE WHEN winner_team_id = home_team_id THEN 'W' ELSE 'L' END AS wl,
                       season_year, 'resolved' AS resolution_status
                FROM dim_game WHERE game_status = 'completed'
                UNION ALL
                SELECT game_id, away_team_id, 'away',
                       CASE WHEN winner_team_id = away_team_id THEN 'W' ELSE 'L' END,
                       season_year, 'resolved'
                FROM dim_game WHERE game_status = 'completed'
                """
            )

    def build_dimensions(self) -> None:
        self.log("Building canonical dimensions")
        self.execute(
            f"""
            CREATE OR REPLACE TABLE dim_team_era AS
            SELECT
              team_history_sk,
              team_id,
              city,
              nickname,
              abbreviation,
              franchise_name,
              league_id,
              TRY_CAST(substr(valid_from, 1, 4) AS INTEGER) AS valid_from_year,
              COALESCE(TRY_CAST(substr(valid_to, 1, 4) AS INTEGER), 9999) AS valid_to_year,
              valid_from,
              valid_to,
              is_current
            FROM {self.src('dim_team_history')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE dim_team AS
            WITH latest_standings AS (
              SELECT *
              FROM {self.src('fact_standings')}
              QUALIFY row_number() OVER (PARTITION BY team_id ORDER BY season_year DESC) = 1
            )
            SELECT
              t.team_id,
              t.abbreviation,
              t.full_name,
              t.city,
              t.state,
              t.arena,
              t.year_founded,
              coalesce(t.conference, s.conference) AS conference,
              coalesce(t.division, s.division) AS division,
              e.franchise_name,
              e.nickname,
              e.valid_from_year,
              e.valid_to_year,
              e.is_current
            FROM {self.src('dim_team')} t
            LEFT JOIN latest_standings s ON s.team_id = t.team_id
            LEFT JOIN dim_team_era e ON e.team_id = t.team_id AND e.is_current
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE dim_player AS
            WITH current_player AS (
              SELECT *
              FROM {self.src('dim_player')}
              WHERE coalesce(is_current, true)
              QUALIFY row_number() OVER (
                PARTITION BY player_id
                ORDER BY coalesce(is_current, false) DESC, TRY_CAST(valid_from AS DATE) DESC NULLS LAST, player_sk DESC NULLS LAST
              ) = 1
            ),
            all_ids AS (
              SELECT person_id AS player_id FROM {self.src('dim_all_players')}
              UNION
              SELECT player_id FROM current_player
              UNION
              SELECT player_id FROM map_player_bbr WHERE is_preferred
            )
            SELECT
              ids.player_id,
              coalesce(cp.full_name, dap.display_first_last, mb.full_name, dbp.player_name) AS full_name,
              cp.first_name,
              cp.last_name,
              coalesce(cp.is_active, dap.roster_status = 1, false) AS is_active,
              cp.team_id AS current_team_id,
              cp.position,
              cp.jersey_number AS current_jersey_number,
              cp.height,
              cp.weight,
              COALESCE(TRY_CAST(cp.birth_date AS DATE), dbp.birth_date) AS birth_date,
              cp.country,
              cp.college_id,
              cp.draft_year,
              cp.draft_round,
              cp.draft_number,
              COALESCE(cp.from_year, TRY_CAST(dap.from_year AS BIGINT), dbp.from_year) AS from_year,
              COALESCE(cp.to_year, TRY_CAST(dap.to_year AS BIGINT), dbp.to_year) AS to_year,
              mb.bbr_player_id,
              dbp.primary_position AS bbr_primary_position,
              dbp.height_inches AS bbr_height_inches,
              dbp.body_weight_lbs AS bbr_weight_lbs,
              dbp.colleges AS bbr_colleges,
              coalesce(dbp.is_hall_of_fame, false) AS is_hall_of_fame,
              CASE
                WHEN cp.player_id IS NOT NULL THEN 'dim_player_current'
                WHEN dap.person_id IS NOT NULL THEN 'dim_all_players'
                WHEN mb.player_id IS NOT NULL THEN 'bbr_crosswalk'
                ELSE 'unresolved'
              END AS canonical_source
            FROM all_ids ids
            LEFT JOIN current_player cp ON cp.player_id = ids.player_id
            LEFT JOIN {self.src('dim_all_players')} dap ON dap.person_id = ids.player_id
            LEFT JOIN map_player_bbr mb ON mb.player_id = ids.player_id AND mb.is_preferred
            LEFT JOIN {self.src('dim_bref_player')} dbp ON dbp.bref_player_id = mb.bbr_player_id
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE dim_game AS
            WITH completed AS (
              SELECT
                game_id,
                CAST(game_date AS DATE) AS game_date,
                game_datetime_est,
                season_year,
                season_type,
                game_type,
                game_subtype,
                game_label,
                game_sub_label,
                series_game_number,
                home_team_id,
                away_team_id,
                home_score,
                away_score,
                winner_team_id,
                arena_id,
                arena_name,
                arena_city,
                arena_state,
                attendance,
                is_overtime,
                'completed' AS game_status,
                'fact_game' AS canonical_source
              FROM {self.src('fact_game')}
            ),
            scheduled AS (
              SELECT
                d.game_id,
                TRY_CAST(d.game_date AS DATE) AS game_date,
                CAST(NULL AS TIMESTAMP) AS game_datetime_est,
                d.season_year,
                d.season_type,
                d.season_type AS game_type,
                CAST(NULL AS VARCHAR) AS game_subtype,
                CAST(NULL AS VARCHAR) AS game_label,
                CAST(NULL AS VARCHAR) AS game_sub_label,
                CAST(NULL AS INTEGER) AS series_game_number,
                d.home_team_id,
                d.visitor_team_id AS away_team_id,
                CAST(NULL AS INTEGER) AS home_score,
                CAST(NULL AS INTEGER) AS away_score,
                CAST(NULL AS BIGINT) AS winner_team_id,
                CAST(NULL AS BIGINT) AS arena_id,
                d.arena_name,
                d.arena_city,
                CAST(NULL AS VARCHAR) AS arena_state,
                CAST(NULL AS INTEGER) AS attendance,
                CAST(NULL AS BOOLEAN) AS is_overtime,
                'scheduled_or_placeholder' AS game_status,
                'dim_game_without_fact_game' AS canonical_source
              FROM {self.src('dim_game')} d
              LEFT JOIN completed c USING (game_id)
              WHERE c.game_id IS NULL
            )
            SELECT * FROM completed
            UNION ALL
            SELECT * FROM scheduled
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE dim_arena AS
            SELECT
              coalesce(arena_id, row_number() OVER (ORDER BY arena_name, arena_city, arena_state) * -1) AS arena_id,
              arena_name,
              arena_city,
              arena_state,
              count(*) AS game_count,
              min(game_date) AS first_game_date,
              max(game_date) AS last_game_date
            FROM dim_game
            WHERE arena_name IS NOT NULL
            GROUP BY arena_id, arena_name, arena_city, arena_state
            """
        )
        if self.has_source_table("dim_official"):
            self.execute(
                f"""
                CREATE OR REPLACE TABLE dim_official AS
                SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
                FROM {self.src('dim_official')}
                """
            )
        else:
            self.execute("CREATE OR REPLACE TABLE dim_official AS SELECT DISTINCT official_id, official_name FROM fact_official_assignment")
        if self.has_source_table("dim_date"):
            self.execute(
                f"""
                CREATE OR REPLACE TABLE dim_date AS
                SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
                FROM {self.src('dim_date')}
                """
            )

    def build_facts(self) -> None:
        self.log("Building canonical facts")
        self.execute(
            """
            CREATE OR REPLACE TABLE fact_game_result AS
            SELECT
              game_id, game_date, game_datetime_est, season_year, season_type,
              home_team_id, away_team_id, home_score, away_score, winner_team_id,
              CASE WHEN winner_team_id = home_team_id THEN away_team_id
                   WHEN winner_team_id = away_team_id THEN home_team_id END AS loser_team_id,
              home_score - away_score AS home_margin,
              abs(home_score - away_score) AS margin,
              is_overtime,
              attendance,
              arena_id,
              'fact_game' AS canonical_source
            FROM dim_game
            WHERE game_status = 'completed'
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_team_game_box AS
            SELECT
              b.game_id,
              b.team_id,
              CASE WHEN b.team_id = g.home_team_id THEN true WHEN b.team_id = g.away_team_id THEN false END AS is_home,
              CASE WHEN b.team_id = g.winner_team_id THEN true WHEN g.winner_team_id IS NULL THEN NULL ELSE false END AS is_win,
              g.season_year,
              g.season_type,
              b.team_name,
              b.team_abbreviation,
              b.team_city,
              b.team_slug,
              b.min,
              b.fgm, b.fga, b.fg_pct,
              b.fg3m, b.fg3a, b.fg3_pct,
              b.ftm, b.fta, b.ft_pct,
              b.oreb, b.dreb, b.reb,
              b.ast, b.stl, b.blk, b.tov, b.pf,
              b.pts,
              b.plus_minus,
              'fact_box_score_team' AS canonical_source
            FROM {self.src('fact_box_score_team')} b
            LEFT JOIN dim_game g ON g.game_id = b.game_id
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_player_game_box AS
            SELECT
              b.game_id,
              b.player_id,
              b.team_id,
              b.opponent_team_id,
              b.is_home,
              b.is_win,
              g.season_year,
              g.season_type,
              g.game_date,
              b.starting_position,
              b.comment,
              b.min,
              b.points AS pts,
              b.assists AS ast,
              b.blocks AS blk,
              b.steals AS stl,
              b.turnovers AS tov,
              b.fga, b.fgm, b.fg_pct,
              b.fg3a, b.fg3m, b.fg3_pct,
              b.fta, b.ftm, b.ft_pct,
              b.oreb, b.dreb, b.reb,
              b.fouls_personal AS pf,
              b.plus_minus,
              b.off_rating, b.def_rating, b.net_rating,
              b.ast_pct, b.ast_to_turnover_ratio, b.ast_ratio,
              b.oreb_pct, b.dreb_pct, b.reb_pct,
              b.tov_pct, b.efg_pct, b.ts_pct, b.usg_pct, b.pace, b.pie,
              'fact_player_game_boxscore' AS canonical_source
            FROM {self.src('fact_player_game_boxscore')} b
            LEFT JOIN dim_game g ON g.game_id = b.game_id
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_player_game_advanced AS
            SELECT
              a.game_id, a.player_id, a.team_id, g.season_year, g.season_type, g.game_date,
              a.off_rating, a.def_rating, a.net_rating,
              a.ast_pct, a.ast_to, a.ast_ratio,
              a.oreb_pct, a.dreb_pct, a.reb_pct,
              a.efg_pct, a.ts_pct, a.usg_pct, a.pace, a.pie, a.poss, a.fta_rate,
              'fact_player_game_advanced' AS canonical_source
            FROM {self.src('fact_player_game_advanced')} a
            LEFT JOIN dim_game g ON g.game_id = a.game_id
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_player_season_box AS
            WITH preferred_bbr AS (
              SELECT bbr_player_id, player_id
              FROM map_player_bbr
              WHERE is_preferred
            ),
            raw AS (
              SELECT
                coalesce(r.person_id, p.player_id) AS player_id,
                r.person_id AS source_person_id,
                r.slug AS bbr_player_id,
                r.team_id,
                end_year_to_season(r.season) AS season_year,
                CASE WHEN r.is_playoffs THEN 'Playoffs' ELSE 'Regular' END AS season_type,
                r.team_abbrev AS source_team_abbreviation,
                r.jersey_number,
                r.gp, r.gs, r.min,
                r.pts, r.fg, r.fga, r.tp, r.tpa, r.ft, r.fta,
                r.orb, r.drb, r.trb, r.ast, r.stl, r.blk, r.tov, r.pf,
                r.per, r.ortg, r.drtg, r.obpm, r.dbpm, r.ows, r.dws, r.vorp,
                r.usgp, r.astp, r.blkp, r.drbp, r.orbp, r.stlp, r.tovp, r.trbp,
                r.td, r.ba, r.onoff100, r.pm100,
                CASE
                  WHEN coalesce(r.person_id, p.player_id) IS NULL THEN 'unresolved_player'
                  WHEN r.person_id IS NULL AND p.player_id IS NOT NULL THEN 'resolved_by_bbr_crosswalk'
                  ELSE 'resolved_source_person_id'
                END AS resolution_status
              FROM {self.src('fact_player_season_stat_resolved')} r
              LEFT JOIN preferred_bbr p ON p.bbr_player_id = r.slug
            )
            SELECT
              raw.player_id,
              raw.source_person_id,
              raw.bbr_player_id,
              raw.team_id,
              coalesce(e.abbreviation, raw.source_team_abbreviation) AS team_abbreviation,
              raw.season_year,
              raw.season_type,
              raw.jersey_number,
              raw.gp, raw.gs,
              raw.min AS total_min,
              raw.pts AS total_pts,
              raw.fg AS total_fgm,
              raw.fga AS total_fga,
              raw.tp AS total_fg3m,
              raw.tpa AS total_fg3a,
              raw.ft AS total_ftm,
              raw.fta AS total_fta,
              raw.orb AS total_oreb,
              raw.drb AS total_dreb,
              coalesce(raw.trb, raw.orb + raw.drb) AS total_reb,
              raw.ast AS total_ast,
              raw.stl AS total_stl,
              raw.blk AS total_blk,
              raw.tov AS total_tov,
              raw.pf AS total_pf,
              raw.pts / nullif(raw.gp, 0) AS avg_pts,
              coalesce(raw.trb, raw.orb + raw.drb) / nullif(raw.gp, 0) AS avg_reb,
              raw.ast / nullif(raw.gp, 0) AS avg_ast,
              raw.fg / nullif(raw.fga, 0) AS fg_pct,
              raw.tp / nullif(raw.tpa, 0) AS fg3_pct,
              raw.ft / nullif(raw.fta, 0) AS ft_pct,
              raw.pts / nullif(2 * (raw.fga + 0.44 * raw.fta), 0) AS ts_pct,
              raw.per, raw.ortg, raw.drtg, raw.obpm, raw.dbpm, raw.ows, raw.dws, raw.vorp,
              raw.usgp, raw.astp, raw.blkp, raw.drbp, raw.orbp, raw.stlp, raw.tovp, raw.trbp,
              raw.td, raw.ba, raw.onoff100, raw.pm100,
              raw.resolution_status,
              'fact_player_season_stat_resolved' AS canonical_source
            FROM raw
            LEFT JOIN dim_team_era e
              ON e.team_id = raw.team_id
             AND season_start_year(raw.season_year) >= e.valid_from_year
             AND season_start_year(raw.season_year) < e.valid_to_year
            QUALIFY row_number() OVER (
              PARTITION BY raw.player_id, raw.team_id, raw.season_year, raw.season_type
              ORDER BY CASE raw.resolution_status WHEN 'resolved_source_person_id' THEN 0 WHEN 'resolved_by_bbr_crosswalk' THEN 1 ELSE 2 END,
                       raw.bbr_player_id
            ) = 1
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_pbp_event AS
            SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
            FROM {self.src('fact_pbp_events')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_pbp_player AS
            SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
            FROM {self.src('bridge_play_player')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_shot AS
            SELECT
              s.game_id, s.player_id, s.team_id, g.season_year, g.season_type, g.game_date,
              s.period, s.minutes_remaining, s.seconds_remaining,
              s.action_type, s.shot_type, s.shot_zone_basic, s.shot_zone_area, s.shot_zone_range,
              s.shot_distance, s.loc_x, s.loc_y, s.shot_made_flag,
              'fact_shot_chart' AS canonical_source
            FROM {self.src('fact_shot_chart')} s
            LEFT JOIN dim_game g ON g.game_id = s.game_id
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_period_score AS
            SELECT
              q.game_id, q.team_id, g.season_year, g.season_type,
              CASE WHEN q.team_id = g.home_team_id THEN 'home'
                   WHEN q.team_id = g.away_team_id THEN 'away'
                   ELSE 'unknown' END AS side,
              q.period, q.pts, q.fgm, q.fga, q.fg3m, q.fg3a, q.ftm, q.fta,
              q.reb, q.ast, q.stl, q.tov, q.plus_minus,
              'fact_game_quarter_scores' AS canonical_source
            FROM {self.src('fact_game_quarter_scores')} q
            LEFT JOIN dim_game g ON g.game_id = q.game_id
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_starting_lineup AS
            SELECT game_id, team_id, person_id AS player_id, starting_position,
                   'fact_starting_lineup_player' AS canonical_source
            FROM {self.src('fact_starting_lineup_player')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_lineup_player AS
            SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
            FROM {self.src('bridge_lineup_player')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_official_assignment AS
            SELECT game_id, official_id, official_name,
                   'fact_game_official' AS canonical_source
            FROM {self.src('fact_game_official')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_standings AS
            SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
            FROM {self.src('fact_standings')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_award AS
            SELECT
              player_id, description, all_nba_team_number, season, month, week,
              conference, award_type, subtype1, subtype2, subtype3,
              'fact_player_awards_bbr_rebuilt' AS canonical_source
            FROM {self.src('fact_player_awards')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_draft AS
            WITH raw AS (
              SELECT
                TRY_CAST(person_id AS BIGINT) AS player_id,
                player_name,
                season AS draft_year,
                round_number, round_pick, overall_pick, draft_type,
                TRY_CAST(team_id AS BIGINT) AS team_id,
                team_city, team_name, team_abbreviation,
                organization, organization_type, player_profile_flag
              FROM {self.src('draft_history')}
            ),
            flagged AS (
              SELECT raw.*,
                     count(*) OVER (PARTITION BY draft_year, overall_pick) AS same_overall_pick_rows
              FROM raw
            )
            SELECT
              *,
              CASE
                WHEN overall_pick IS NULL THEN 'unknown_pick'
                WHEN overall_pick <= 0 THEN 'territorial_or_unranked'
                WHEN same_overall_pick_rows > 1 THEN 'duplicate_source_slot'
                ELSE 'unique_slot'
              END AS draft_slot_status,
              'draft_history_bbr_rebuilt' AS canonical_source
            FROM flagged
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_player_jersey_season AS
            SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
            FROM {self.src('fact_player_jersey_season')}
            """
        )
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_coach_season AS
            SELECT * EXCLUDE (_ingest_run_id, _source_system, _source_table, _source_record_hash, _normalized_game_id)
            FROM {self.src('fact_coach_season')}
            """
        )
        self.build_odds_facts()

    def build_odds_facts(self) -> None:
        self.execute(
            f"""
            CREATE OR REPLACE TABLE fact_game_odds AS
            SELECT
              b.game_id,
              o.snapshot_ts,
              o.market,
              o.selection,
              o.odds,
              o.matchup,
              o.is_preseason,
              b.team1_id,
              b.team2_id,
              b.match_method,
              b.match_confidence,
              b.is_ambiguous,
              b.is_unresolved,
              'fact_game_market_odds+bridge_game_market_odds' AS canonical_source
            FROM {self.src('fact_game_market_odds')} o
            LEFT JOIN {self.src('bridge_game_market_odds')} b
              ON b.matchup = o.matchup AND b.snapshot_ts = o.snapshot_ts
            UNION ALL
            SELECT
              l.game_id,
              CAST(NULL AS TIMESTAMP) AS snapshot_ts,
              metric.market,
              metric.selection,
              metric.odds,
              CAST(NULL AS VARCHAR) AS matchup,
              false AS is_preseason,
              CAST(NULL AS BIGINT) AS team1_id,
              CAST(NULL AS BIGINT) AS team2_id,
              'direct_game_id' AS match_method,
              1.0 AS match_confidence,
              false AS is_ambiguous,
              false AS is_unresolved,
              'fact_game_betting_lines' AS canonical_source
            FROM {self.src('fact_game_betting_lines')} l,
            LATERAL (
              VALUES
                ('decimal_home', 'home', l.decimal_home),
                ('decimal_away', 'away', l.decimal_away),
                ('spread_home', 'home', l.spread_home),
                ('spread_away', 'away', l.spread_away),
                ('total', 'game', l.total)
            ) AS metric(market, selection, odds)
            WHERE metric.odds IS NOT NULL
            """
        )

    def build_marts(self) -> None:
        self.log("Building derived marts from canonical facts")
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_player_season AS
            WITH bbr AS (
              SELECT
                player_id, team_id, team_abbreviation, season_year, season_type,
                gp, total_min, total_min / nullif(gp, 0) AS avg_min,
                total_pts, avg_pts,
                total_reb, avg_reb,
                total_ast, avg_ast,
                total_stl, total_stl / nullif(gp, 0) AS avg_stl,
                total_blk, total_blk / nullif(gp, 0) AS avg_blk,
                total_tov, total_tov / nullif(gp, 0) AS avg_tov,
                total_fgm, total_fga, fg_pct,
                total_fg3m, total_fg3a, fg3_pct,
                total_ftm, total_fta, ft_pct,
                ortg AS avg_off_rating,
                drtg AS avg_def_rating,
                ortg - drtg AS avg_net_rating,
                ts_pct AS avg_ts_pct,
                usgp / 100.0 AS avg_usg_pct,
                CAST(NULL AS DOUBLE) AS avg_pie,
                'fact_player_season_box' AS canonical_source
              FROM fact_player_season_box
              WHERE player_id IS NOT NULL
            ),
            cup AS (
              SELECT
                p.player_id, p.team_id, max(t.abbreviation) AS team_abbreviation,
                p.season_year, p.season_type,
                count(*) AS gp,
                sum(min) AS total_min, avg(min) AS avg_min,
                sum(pts) AS total_pts, avg(pts) AS avg_pts,
                sum(reb) AS total_reb, avg(reb) AS avg_reb,
                sum(ast) AS total_ast, avg(ast) AS avg_ast,
                sum(stl) AS total_stl, avg(stl) AS avg_stl,
                sum(blk) AS total_blk, avg(blk) AS avg_blk,
                sum(tov) AS total_tov, avg(tov) AS avg_tov,
                sum(fgm) AS total_fgm, sum(fga) AS total_fga, sum(fgm) / nullif(sum(fga), 0) AS fg_pct,
                sum(fg3m) AS total_fg3m, sum(fg3a) AS total_fg3a, sum(fg3m) / nullif(sum(fg3a), 0) AS fg3_pct,
                sum(ftm) AS total_ftm, sum(fta) AS total_fta, sum(ftm) / nullif(sum(fta), 0) AS ft_pct,
                CAST(NULL AS DOUBLE) AS avg_off_rating,
                CAST(NULL AS DOUBLE) AS avg_def_rating,
                CAST(NULL AS DOUBLE) AS avg_net_rating,
                sum(pts) / nullif(2 * (sum(fga) + 0.44 * sum(fta)), 0) AS avg_ts_pct,
                CAST(NULL AS DOUBLE) AS avg_usg_pct,
                CAST(NULL AS DOUBLE) AS avg_pie,
                'fact_player_game_box_cup_rollup' AS canonical_source
              FROM fact_player_game_box p
              LEFT JOIN dim_team t ON t.team_id = p.team_id
              WHERE season_type = 'Cup'
              GROUP BY p.player_id, p.team_id, p.season_year, p.season_type
            )
            SELECT * FROM bbr
            UNION ALL
            SELECT * FROM cup
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_player_career AS
            SELECT
              player_id,
              sum(gp) AS career_gp,
              sum(total_min) AS career_min,
              sum(total_pts) AS career_pts,
              sum(total_pts) / nullif(sum(gp), 0) AS career_ppg,
              sum(total_reb) / nullif(sum(gp), 0) AS career_rpg,
              sum(total_ast) / nullif(sum(gp), 0) AS career_apg,
              sum(total_stl) / nullif(sum(gp), 0) AS career_spg,
              sum(total_blk) / nullif(sum(gp), 0) AS career_bpg,
              sum(total_fgm) / nullif(sum(total_fga), 0) AS career_fg_pct,
              sum(total_fg3m) / nullif(sum(total_fg3a), 0) AS career_fg3_pct,
              sum(total_ftm) / nullif(sum(total_fta), 0) AS career_ft_pct,
              min(season_year) AS first_season,
              max(season_year) AS last_season,
              count(DISTINCT season_year) AS seasons_played
            FROM mart_player_season
            WHERE season_type = 'Regular'
            GROUP BY 1
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_player_rolling AS
            SELECT
              game_id, player_id, game_date,
              avg(pts) OVER w5 AS pts_roll5,
              avg(reb) OVER w5 AS reb_roll5,
              avg(ast) OVER w5 AS ast_roll5,
              avg(pts) OVER w10 AS pts_roll10,
              avg(reb) OVER w10 AS reb_roll10,
              avg(ast) OVER w10 AS ast_roll10,
              avg(pts) OVER w20 AS pts_roll20,
              avg(reb) OVER w20 AS reb_roll20,
              avg(ast) OVER w20 AS ast_roll20
            FROM fact_player_game_box
            WHERE player_id IS NOT NULL AND game_date IS NOT NULL
            WINDOW
              w5 AS (PARTITION BY player_id ORDER BY game_date, game_id ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
              w10 AS (PARTITION BY player_id ORDER BY game_date, game_id ROWS BETWEEN 9 PRECEDING AND CURRENT ROW),
              w20 AS (PARTITION BY player_id ORDER BY game_date, game_id ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_shot_zones AS
            SELECT
              player_id, season_year, shot_zone_basic, shot_zone_area, shot_zone_range,
              count(*) AS attempts,
              sum(CASE WHEN shot_made_flag = 1 THEN 1 ELSE 0 END) AS makes,
              sum(CASE WHEN shot_made_flag = 1 THEN 1 ELSE 0 END)::DOUBLE / nullif(count(*), 0) AS fg_pct,
              avg(shot_distance) AS avg_distance
            FROM fact_shot
            WHERE player_id IS NOT NULL
            GROUP BY 1, 2, 3, 4, 5
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_franchise_leaders AS
            WITH per_team AS (
              SELECT team_id, player_id,
                     sum(total_pts) AS pts,
                     sum(total_ast) AS ast,
                     sum(total_reb) AS reb,
                     sum(total_blk) AS blk,
                     sum(total_stl) AS stl
              FROM mart_player_season
              WHERE season_type = 'Regular' AND team_id IS NOT NULL
              GROUP BY 1, 2
            ),
            ranked AS (
              SELECT *,
                     row_number() OVER (PARTITION BY team_id ORDER BY pts DESC NULLS LAST) AS pts_rank,
                     row_number() OVER (PARTITION BY team_id ORDER BY ast DESC NULLS LAST) AS ast_rank,
                     row_number() OVER (PARTITION BY team_id ORDER BY reb DESC NULLS LAST) AS reb_rank,
                     row_number() OVER (PARTITION BY team_id ORDER BY blk DESC NULLS LAST) AS blk_rank,
                     row_number() OVER (PARTITION BY team_id ORDER BY stl DESC NULLS LAST) AS stl_rank
              FROM per_team
            )
            SELECT
              team_id,
              max(pts) FILTER (WHERE pts_rank = 1) AS pts,
              max(player_id) FILTER (WHERE pts_rank = 1) AS pts_player_id,
              max(ast) FILTER (WHERE ast_rank = 1) AS ast,
              max(player_id) FILTER (WHERE ast_rank = 1) AS ast_player_id,
              max(reb) FILTER (WHERE reb_rank = 1) AS reb,
              max(player_id) FILTER (WHERE reb_rank = 1) AS reb_player_id,
              max(blk) FILTER (WHERE blk_rank = 1) AS blk,
              max(player_id) FILTER (WHERE blk_rank = 1) AS blk_player_id,
              max(stl) FILTER (WHERE stl_rank = 1) AS stl,
              max(player_id) FILTER (WHERE stl_rank = 1) AS stl_player_id
            FROM ranked
            GROUP BY 1
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_draft_value AS
            SELECT
              d.player_id AS person_id,
              d.draft_year AS season,
              d.round_number,
              d.round_pick,
              d.overall_pick,
              d.team_id,
              d.player_name,
              p.position,
              p.country,
              c.career_gp,
              c.career_pts,
              c.career_ppg,
              c.career_rpg,
              c.career_apg,
              c.career_fg_pct,
              c.career_fg3_pct,
              c.seasons_played,
              c.first_season,
              c.last_season
            FROM fact_draft d
            LEFT JOIN mart_player_career c ON c.player_id = d.player_id
            LEFT JOIN dim_player p ON p.player_id = d.player_id
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_head_to_head AS
            WITH sides AS (
              SELECT game_id, season_year, home_team_id AS team_id, away_team_id AS opponent_team_id,
                     home_score AS pts_for, away_score AS pts_against,
                     CASE WHEN winner_team_id = home_team_id THEN 1 ELSE 0 END AS win
              FROM fact_game_result
              UNION ALL
              SELECT game_id, season_year, away_team_id, home_team_id,
                     away_score, home_score,
                     CASE WHEN winner_team_id = away_team_id THEN 1 ELSE 0 END
              FROM fact_game_result
            )
            SELECT
              team_id, opponent_team_id, season_year,
              count(*) AS games_played,
              sum(win) AS wins,
              count(*) - sum(win) AS losses,
              avg(pts_for) AS avg_pts_scored,
              avg(pts_against) AS avg_pts_allowed,
              avg(pts_for - pts_against) AS avg_margin
            FROM sides
            GROUP BY 1, 2, 3
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_betting_summary AS
            SELECT
              game_id,
              count(*) AS odds_rows,
              min(snapshot_ts) AS first_snapshot_ts,
              max(snapshot_ts) AS last_snapshot_ts,
              avg(odds) FILTER (WHERE market LIKE '%decimal%' OR market LIKE '%Money%') AS avg_moneyline_decimal,
              avg(odds) FILTER (WHERE market LIKE '%spread%') AS avg_spread,
              avg(odds) FILTER (WHERE market LIKE '%total%' OR market LIKE '%Total%') AS avg_total
            FROM fact_game_odds
            WHERE game_id IS NOT NULL
            GROUP BY 1
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE mart_league_leaders AS
            WITH base AS (
              SELECT *,
                     row_number() OVER (PARTITION BY season_year, season_type ORDER BY avg_pts DESC NULLS LAST) AS rank_pts,
                     row_number() OVER (PARTITION BY season_year, season_type ORDER BY avg_reb DESC NULLS LAST) AS rank_reb,
                     row_number() OVER (PARTITION BY season_year, season_type ORDER BY avg_ast DESC NULLS LAST) AS rank_ast
              FROM mart_player_season
              WHERE gp >= 1
            )
            SELECT season_year, season_type, player_id, team_id, gp,
                   avg_pts, rank_pts, avg_reb, rank_reb, avg_ast, rank_ast
            FROM base
            WHERE rank_pts <= 250 OR rank_reb <= 250 OR rank_ast <= 250
            """
        )
        self.execute(
            """
            CREATE OR REPLACE TABLE analytics_player_game_complete AS
            SELECT
              b.*,
              p.full_name,
              t.abbreviation AS team_abbreviation,
              a.off_rating AS advanced_off_rating,
              a.def_rating AS advanced_def_rating,
              a.net_rating AS advanced_net_rating,
              a.pie AS advanced_pie
            FROM fact_player_game_box b
            LEFT JOIN dim_player p ON p.player_id = b.player_id
            LEFT JOIN dim_team t ON t.team_id = b.team_id
            LEFT JOIN fact_player_game_advanced a
              ON a.game_id = b.game_id AND a.player_id = b.player_id AND a.team_id = b.team_id
            """
        )

    def add_lineage(self) -> None:
        self.log("Recording column lineage")
        # Source-copy lineage: each original column flows unchanged into src_*.
        rows = []
        for table in self.source_tables:
            for col in self.source_columns(table.name):
                rows.append((table.source_copy_name, col, table.name, col, "lossless source copy"))
        if rows:
            self.c.executemany("INSERT INTO meta_column_lineage VALUES (?, ?, ?, ?, ?)", rows)
        target_sources = {
            "map_player_source_id": "bridge_player_source_id",
            "map_team_source_id": "bridge_team_source_id",
            "map_game_source_id": "bridge_game_source_id",
            "dim_player": "dim_player + dim_all_players + bridge_player_bbr + dim_bref_player",
            "dim_team": "dim_team + dim_team_history + fact_standings",
            "dim_game": "fact_game + dim_game placeholders",
            "fact_game_result": "dim_game completed games",
            "fact_team_game_box": "fact_box_score_team + dim_game",
            "fact_player_game_box": "fact_player_game_boxscore + dim_game",
            "fact_player_season_box": "fact_player_season_stat_resolved + map_player_bbr + dim_team_era",
            "fact_pbp_event": "fact_pbp_events",
            "fact_shot": "fact_shot_chart + dim_game",
            "fact_standings": "fact_standings",
            "fact_award": "fact_player_awards",
            "fact_draft": "draft_history",
            "fact_player_jersey_season": "fact_player_jersey_season",
            "fact_coach_season": "fact_coach_season",
            "mart_player_season": "fact_player_season_box + fact_player_game_box cup rollup",
            "mart_player_career": "mart_player_season",
            "mart_shot_zones": "fact_shot",
            "mart_head_to_head": "fact_game_result",
        }
        for target, source in target_sources.items():
            if not self.table_exists(target):
                continue
            cols = [r[0] for r in self.c.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position", [target]).fetchall()]
            self.c.executemany(
                "INSERT INTO meta_column_lineage VALUES (?, ?, ?, NULL, ?)",
                [(target, col, source, "canonical transform") for col in cols],
            )

    def table_exists(self, table: str) -> bool:
        return bool(self.scalar("SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_name = ?", [table]))

    def record_check(self, group: str, name: str, ok: bool, severity: str, observed: object, expected: object, details: str = "") -> None:
        self.execute(
            """
            INSERT INTO meta_quality_check
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
            """,
            [
                self.run_id,
                group,
                name,
                "PASS" if ok else ("WARN" if severity == "warn" else "FAIL"),
                severity,
                str(observed),
                str(expected),
                details,
            ],
        )

    def run_quality_checks(self) -> None:
        self.log("Running quality checks")
        mismatches = self.scalar(
            "SELECT count(*) FROM meta_source_table WHERE original_row_count <> source_row_count"
        )
        self.record_check("source", "source_row_parity", int(mismatches) == 0, "fail", mismatches, 0)

        empty_tables = self.scalar("SELECT count(*) FROM meta_table_fate WHERE fate = 'empty_endpoint_shell'")
        self.record_check("source", "empty_endpoint_shells_classified", True, "warn", empty_tables, "documented")

        legacy_tables = self.scalar("SELECT count(*) FROM meta_table_fate WHERE fate = 'legacy_do_not_use'")
        self.record_check("source", "legacy_tables_classified", True, "info", legacy_tables, "documented")

        checks = [
            ("map", "unique_player_source_id", "map_player_source_id", "source_system, source_id"),
            ("map", "unique_team_source_id", "map_team_source_id", "source_system, source_id"),
            ("map", "unique_game_source_id", "map_game_source_id", "source_system, source_id"),
        ]
        for group, name, table, key in checks:
            dupes = self.scalar(f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY {key} HAVING count(*) > 1)")
            self.record_check(group, name, int(dupes) == 0, "fail", dupes, 0)

        player_resolved = self.scalar("SELECT count(*) FROM map_player_source_id WHERE source_system='kaggle_nba' AND resolution_status='resolved'")
        self.record_check("map", "kaggle_player_resolution_floor", int(player_resolved) >= 6692, "fail", player_resolved, ">=6692")
        game_resolved = self.scalar("SELECT count(*) FROM map_game_source_id WHERE source_system='kaggle_nba' AND resolution_status='resolved'")
        self.record_check("map", "kaggle_game_resolution_floor", int(game_resolved) >= 73348, "fail", game_resolved, ">=73348")

        # bbr rows should never sit unresolved/ambiguous when map_player_bbr
        # already has a preferred match for that bbr id -- catches a
        # data/ingest/ingest.py --reconcile-bbr run being skipped.
        bbr_reconcilable = self.scalar(
            """
            SELECT count(*) FROM map_player_source_id m
            JOIN map_player_bbr b ON b.bbr_player_id = m.source_id AND b.is_preferred
            WHERE m.source_system IN ('basketball_reference', 'json_slug')
              AND m.resolution_status <> 'resolved'
            """
        )
        self.record_check("map", "no_bbr_reconcilable_unresolved", int(bbr_reconcilable) == 0, "fail", bbr_reconcilable, 0)

        # kaggle unresolved rows should mostly carry a specific classified
        # reason (see data/ingest/ingest.py classify_kaggle_unresolved_players);
        # only rows with no joinable play-by-play row fall back to the
        # generic reason (confirmed ceiling: 28 at reconciliation time).
        kaggle_generic_reason = self.scalar(
            """
            SELECT count(*) FROM map_player_source_id
            WHERE source_system = 'kaggle_nba' AND resolution_status = 'unresolved'
              AND unresolved_reason = 'not_in_dim_all_players_or_non_player_pbp_person'
            """
        )
        self.record_check("map", "kaggle_unresolved_reason_specificity", int(kaggle_generic_reason) <= 28, "fail",
                           kaggle_generic_reason, "<=28")

        # remaining bbr ids with no bridge_player_bbr candidate at all are a
        # documented backlog (meta_known_gap: bbr_bridge_residual_unresolved_players,
        # measured at 401 when that gap row was written) -- flag only if the
        # gap grows well past that, not on the expected residual itself.
        bbr_residual = self.scalar(
            """
            SELECT count(*) FROM map_player_source_id m
            WHERE m.source_system IN ('basketball_reference', 'json_slug')
              AND m.resolution_status <> 'resolved'
              AND NOT EXISTS (SELECT 1 FROM map_player_bbr b WHERE b.bbr_player_id = m.source_id)
            """
        )
        self.record_check("map", "bbr_residual_gap_within_ceiling", int(bbr_residual) <= 450, "fail",
                           bbr_residual, "<=450 (see meta_known_gap: bbr_bridge_residual_unresolved_players)")

        game_count = self.scalar("SELECT count(*) FROM fact_game_result")
        src_game_count = self.scalar(f"SELECT count(*) FROM {self.src('fact_game')}")
        self.record_check("game", "fact_game_result_row_parity", int(game_count) == int(src_game_count), "fail", game_count, src_game_count)

        playoff_2026 = self.scalar("SELECT count(*) FROM fact_game_result WHERE season_year='2025-26' AND season_type='Playoffs'")
        self.record_check("game", "playoff_2026_complete", int(playoff_2026) == 85, "fail", playoff_2026, 85)

        bad_period_sides = self.scalar("SELECT count(*) FROM fact_period_score WHERE side = 'unknown'")
        self.record_check("game", "period_score_team_side_resolved", int(bad_period_sides) == 0, "fail", bad_period_sides, 0)

        pbp_count = self.scalar("SELECT count(*) FROM fact_pbp_event")
        src_pbp_count = self.scalar(f"SELECT count(*) FROM {self.src('fact_pbp_events')}")
        self.record_check("pbp", "pbp_event_row_parity", int(pbp_count) == int(src_pbp_count), "fail", pbp_count, src_pbp_count)

        shot_orphans = self.scalar("SELECT count(*) FROM fact_shot s LEFT JOIN dim_game g USING (game_id) WHERE g.game_id IS NULL")
        self.record_check("shots", "shot_game_join_orphans", int(shot_orphans) == 0, "fail", shot_orphans, 0)

        season_dupes = self.scalar(
            """
            SELECT count(*) FROM (
              SELECT player_id, team_id, season_year, season_type
              FROM fact_player_season_box
              GROUP BY 1, 2, 3, 4 HAVING count(*) > 1
            )
            """
        )
        self.record_check("player_season", "no_player_season_fanout", int(season_dupes) == 0, "fail", season_dupes, 0)

        standings_mismatches = self.scalar(
            f"""
            WITH bbr AS (
              SELECT end_year_to_season(season) AS season_year,
                     nba_team_id AS team_id,
                     CAST(w AS BIGINT) AS bbr_wins,
                     CAST(l AS BIGINT) AS bbr_losses
              FROM {self.src('stg_bref_team_summaries')}
              WHERE nba_team_id IS NOT NULL
            )
            SELECT count(*)
            FROM fact_standings s
            JOIN bbr USING (season_year, team_id)
            WHERE s.season_type = 'Regular'
              AND (s.wins <> bbr.bbr_wins OR s.losses <> bbr.bbr_losses)
            """
        )
        self.record_check("standings", "regular_standings_match_bbr_wl", int(standings_mismatches) == 0, "fail", standings_mismatches, 0)

        luka_awards = self.scalar("SELECT count(*) FROM fact_award WHERE player_id = 1629029")
        self.record_check("awards", "luka_doncic_awards_present", int(luka_awards) >= 20, "fail", luka_awards, ">=20")

        kobe_8 = self.scalar(
            """
            SELECT count(*) FROM fact_player_jersey_season
            WHERE player_id = 977 AND team_abbreviation = 'LAL'
              AND season_year IN ('1996-97', '2005-06')
              AND jersey_number = '8'
            """
        )
        self.record_check("jersey", "kobe_8_bbr_era_rows", int(kobe_8) == 2, "fail", kobe_8, 2)

        harden_13 = self.scalar(
            """
            SELECT count(*) FROM fact_player_jersey_season
            WHERE player_id = 201935 AND team_abbreviation = 'HOU'
              AND season_year = '2020-21' AND jersey_number = '13'
            """
        )
        self.record_check("jersey", "harden_hou_2020_13", int(harden_13) == 1, "fail", harden_13, 1)

        bad_jersey_sources = self.scalar(
            """
            SELECT count(*) FROM fact_player_jersey_season
            WHERE lower(source) LIKE '%espn%' OR lower(source) LIKE '%cumulative%'
            """
        )
        self.record_check("jersey", "current_number_sources_suppressed", int(bad_jersey_sources) == 0, "fail", bad_jersey_sources, 0)

        unclassified_draft_dupes = self.scalar(
            """
            SELECT count(*) FROM (
              SELECT draft_year, overall_pick
              FROM fact_draft
              WHERE overall_pick > 0
              GROUP BY 1, 2
              HAVING count(*) > 1
                 AND NOT bool_or(draft_slot_status = 'duplicate_source_slot')
            )
            """
        )
        duplicate_draft_slots = self.scalar(
            """
            SELECT count(*) FROM (
              SELECT draft_year, overall_pick
              FROM fact_draft
              WHERE overall_pick > 0
              GROUP BY 1, 2 HAVING count(*) > 1
            )
            """
        )
        self.record_check(
            "draft",
            "draft_duplicate_slots_classified",
            int(unclassified_draft_dupes) == 0,
            "fail",
            f"{duplicate_draft_slots} duplicate slots; {unclassified_draft_dupes} unclassified",
            "all duplicates classified",
        )

        abbrev_mismatches = self.scalar(
            """
            SELECT count(*)
            FROM mart_player_season m
            JOIN dim_team_era e
              ON e.team_id = m.team_id
             AND season_start_year(m.season_year) >= e.valid_from_year
             AND season_start_year(m.season_year) < e.valid_to_year
            WHERE m.season_type = 'Regular'
              AND m.team_abbreviation IS NOT NULL
              AND m.team_abbreviation <> e.abbreviation
            """
        )
        self.record_check("team_era", "mart_player_season_team_abbrev_current_era", int(abbrev_mismatches) == 0, "fail", abbrev_mismatches, 0)

    def complete(self, status: str) -> None:
        self.execute(
            "UPDATE meta_build_run SET completed_at = current_timestamp, status = ? WHERE build_run_id = ?",
            [status, self.run_id],
        )

    def quality_summary(self) -> tuple[int, int, int]:
        rows = self.c.execute(
            """
            SELECT
              count(*) FILTER (WHERE status = 'FAIL') AS fails,
              count(*) FILTER (WHERE status = 'WARN') AS warns,
              count(*) AS total
            FROM meta_quality_check
            WHERE build_run_id = ?
            """,
            [self.run_id],
        ).fetchone()
        return int(rows[0] or 0), int(rows[1] or 0), int(rows[2] or 0)

    def build(self) -> tuple[int, int, int]:
        self.load_source_catalog()
        self.connect()
        try:
            self.create_metadata_tables()
            self.create_macros()
            self.create_source_layer()
            self.populate_table_fates()
            self.build_maps()
            self.build_dimensions()
            self.build_facts()
            self.build_marts()
            self.add_lineage()
            self.run_quality_checks()
            fails, warns, total = self.quality_summary()
            self.complete("failed_quality" if fails else "complete")
            self.log(f"Quality checks: {total} total, {fails} FAIL, {warns} WARN")
            return fails, warns, total
        except Exception:
            self.complete("error")
            raise
        finally:
            self.c.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB,
                        help="Raw warehouse to build from. No default -- data/nba.duckdb is now "
                             "itself a built artifact; pass the archived raw warehouse path explicitly.")
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--replace", action="store_true", help="Delete the target DB before building.")
    parser.add_argument(
        "--source-mode",
        choices=["copy", "view"],
        default="copy",
        help="copy materializes src_* tables; view is a fast smoke-test mode.",
    )
    parser.add_argument(
        "--skip-source-hashes",
        action="store_true",
        help="Skip bit_xor source hash checks. Useful for smoke tests.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=None,
        help="Minimum free space required for copy mode. Defaults to max(60GB, 3x source DB size).",
    )
    parser.add_argument(
        "--force-low-disk",
        action="store_true",
        help="Allow copy mode even when the preflight disk-space check fails.",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--allow-quality-failures",
        action="store_true",
        help="Exit 0 even when quality checks fail.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.source_db is None:
        print("--source-db is required (no default raw warehouse; see module docstring)", file=sys.stderr)
        return 2
    source_db = args.source_db.resolve()
    target_db = args.target_db.resolve()
    if not source_db.exists():
        print(f"source database not found: {source_db}", file=sys.stderr)
        return 2
    if source_db == target_db:
        print("target database must be different from source database", file=sys.stderr)
        return 2
    if target_db.exists():
        if not args.replace:
            print(f"target database already exists: {target_db}; pass --replace", file=sys.stderr)
            return 2
        target_db.unlink()
        wal = target_db.with_suffix(target_db.suffix + ".wal")
        if wal.exists():
            wal.unlink()
    target_db.parent.mkdir(parents=True, exist_ok=True)
    if args.source_mode == "copy" and not args.force_low_disk:
        free_gb = shutil.disk_usage(target_db.parent).free / (1024**3)
        source_gb = source_db.stat().st_size / (1024**3)
        min_free_gb = args.min_free_gb if args.min_free_gb is not None else max(60.0, source_gb * 3.0)
        if free_gb < min_free_gb:
            print(
                "copy mode needs more free disk space: "
                f"{free_gb:.2f}GB free, {min_free_gb:.2f}GB required. "
                "Use --source-mode view for a canonical smoke build, free disk space, "
                "or pass --force-low-disk to override.",
                file=sys.stderr,
            )
            return 2
    builder = WarehouseBuilder(
        source_db=source_db,
        target_db=target_db,
        source_mode=args.source_mode,
        source_hashes=not args.skip_source_hashes,
        verbose=args.verbose,
    )
    print(
        json.dumps(
            {
                "source_db": str(source_db),
                "target_db": str(target_db),
                "source_mode": args.source_mode,
                "source_hashes": not args.skip_source_hashes,
            },
            indent=2,
        ),
        flush=True,
    )
    fails, _warns, _total = builder.build()
    return 0 if args.allow_quality_failures or fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
