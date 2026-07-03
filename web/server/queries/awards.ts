import type { DuckDBValue } from "@duckdb/node-api";
import { queryObjects } from "../db.ts";
import { PLAYER_AWARD_ROWS_CTE, PLAYER_BBR_XWALK_CTE, type Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Awards
// ---------------------------------------------------------------------------

export async function listAwardSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season: string }>(
    `WITH ${PLAYER_AWARD_ROWS_CTE}
     SELECT DISTINCT season FROM award_rows ORDER BY season DESC`,
  );
  return rows.map((r) => r.season);
}

export async function listAwardTypes(): Promise<string[]> {
  const rows = await queryObjects<{ award_type: string }>(
    `WITH ${PLAYER_AWARD_ROWS_CTE}
     SELECT DISTINCT award_type
     FROM award_rows
     ORDER BY award_type`,
  );
  return rows.map((r) => r.award_type);
}

export async function getAwards(season: string, awardType: string | null): Promise<Row[]> {
  const conditions = ["a.season = ?"];
  const params: DuckDBValue[] = [season];
  if (awardType) {
    conditions.push("a.award_type = ?");
    params.push(awardType);
  }
  return queryObjects(
    `WITH ${PLAYER_AWARD_ROWS_CTE}
     SELECT a.*, COALESCE(p.full_name, a.source_player_name) AS full_name
     FROM award_rows a
     LEFT JOIN dim_player p ON p.player_id = a.player_id AND p.is_current
     WHERE ${conditions.join(" AND ")}
     ORDER BY a.award_type, full_name`,
    params,
  );
}

// ---------------------------------------------------------------------------
// Award voting detail (BBR voting shares via the crosswalk)
// ---------------------------------------------------------------------------

export async function getAwardVoting(season: string, award: string): Promise<Row[]> {
  return queryObjects(
    `WITH ${PLAYER_BBR_XWALK_CTE},
     bref_name_span_xwalk AS (
       SELECT normalized_player_name, nba_player_id, "from" AS from_year, "to" AS to_year
       FROM stg_bref_player_career_info
       WHERE nba_player_id IS NOT NULL
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY normalized_player_name, "from", "to"
         ORDER BY nba_player_id
       ) = 1
     )
     SELECT COALESCE(s.nba_player_id, x.nba_player_id, nx.nba_player_id) AS player_id,
            coalesce(p.full_name, s.player) AS full_name,
            s.age, s.first AS first_place_votes,
            s.pts_won, s.pts_max, s.share, s.winner
     FROM stg_bref_player_award_shares s
     LEFT JOIN player_bbr_xwalk x ON x.bbr_player_id = s.bref_player_id
     LEFT JOIN bref_name_span_xwalk nx
       ON s.nba_player_id IS NULL
       AND s.bref_player_id IS NULL
       AND nx.normalized_player_name = s.normalized_player_name
       AND s.season BETWEEN nx.from_year AND nx.to_year
     LEFT JOIN dim_player p
       ON p.player_id = COALESCE(s.nba_player_id, x.nba_player_id, nx.nba_player_id)
       AND p.is_current
     WHERE s.season = TRY_CAST(? AS INTEGER) AND s.award = ?
     ORDER BY s.pts_won DESC NULLS LAST, s.share DESC NULLS LAST`,
    [season, award],
  );
}
