"""(Re)write backend/tests/golden/golden.csv using Python's csv writer so SQL
fields with embedded commas are properly quoted.

Run from the repo root:
    python backend/tests/golden/build_golden_csv.py
"""

from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (golden_id, entity_type, identifier, season_end_year, is_playoffs, team_id,
#  stat_key, expected_value, sql_query, source_type, anchor_path, source_url,
#  caveat)
ROWS = [
    (
        "curry_2016_3pm",
        "player_season",
        "curryst01",
        2016,
        False,
        1610612744,
        "3P",
        402,
        'SELECT "3P" FROM api.v_canonical_player_season_totals '
        "WHERE PLAYER_ID = 'curryst01' AND SEASON = 2016 AND TEAM_ABBR = 'GSW'",
        "anchor_html",
        "player_career_stats/curryst01.html",
        "https://www.basketball-reference.com/players/c/curryst01.html",
        "",
    ),
    (
        "curry_2016_pts",
        "player_season",
        "curryst01",
        2016,
        False,
        1610612744,
        "PTS",
        2375,
        "SELECT PTS FROM api.v_canonical_player_season_totals "
        "WHERE PLAYER_ID = 'curryst01' AND SEASON = 2016 AND TEAM_ABBR = 'GSW'",
        "anchor_html",
        "player_career_stats/curryst01.html",
        "https://www.basketball-reference.com/players/c/curryst01.html",
        "",
    ),
    (
        "jordan_1987_pts",
        "player_season",
        "jordami01",
        1987,
        False,
        0,
        "PTS",
        3041,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'jordami01') "
        "  AND s.season_year = '1987' "
        "  AND s.is_playoffs = false "
        "  AND s.team_id = (SELECT DISTINCT team_id "
        "        FROM unified_star.fact_player_season_stats "
        "        WHERE player_id = (SELECT player_id FROM unified_star.dim_player "
        "                WHERE bref_player_id = 'jordami01') "
        "          AND season_year = '1987' AND is_playoffs = false LIMIT 1)",
        "anchor_html",
        "player_career_stats/jordami01.html",
        "https://www.basketball-reference.com/players/j/jordami01.html",
        "",
    ),
    (
        "wilt_1962_pts",
        "player_season",
        "chambwi01",
        1962,
        False,
        0,
        "PTS",
        4029,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND s.season_year = '1962' "
        "  AND s.is_playoffs = false "
        "  AND s.team_id = (SELECT DISTINCT team_id "
        "        FROM unified_star.fact_player_season_stats "
        "        WHERE player_id = (SELECT player_id FROM unified_star.dim_player "
        "                WHERE bref_player_id = 'chambwi01') "
        "          AND season_year = '1962' AND is_playoffs = false LIMIT 1)",
        "anchor_html",
        "player_career_stats/chambwi01.html",
        "https://www.basketball-reference.com/players/c/chambwi01.html",
        "pre_1973_anchor_stl_blk_also_asserted_null",
    ),
    (
        "wilt_1962_stl_null",
        "player_season",
        "chambwi01",
        1962,
        False,
        0,
        "STL",
        None,
        "SELECT s.stl FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND s.season_year = '1962' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/chambwi01.html",
        "https://www.basketball-reference.com/players/c/chambwi01.html",
        "pre_1973_anchor_assert_null",
    ),
    (
        "wilt_1962_blk_null",
        "player_season",
        "chambwi01",
        1962,
        False,
        0,
        "BLK",
        None,
        "SELECT s.blk FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND s.season_year = '1962' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/chambwi01.html",
        "https://www.basketball-reference.com/players/c/chambwi01.html",
        "pre_1973_anchor_assert_null",
    ),
    (
        "kobe_81_game",
        "game_boxscore",
        "bryanko01",
        2006,
        False,
        1610612747,
        "PTS",
        81,
        "SELECT DISTINCT b.points FROM unified_star.fact_player_game_boxscore b "
        "JOIN api.v_game_summary g ON b.game_id = g.game_id "
        "WHERE b.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'bryanko01') "
        "  AND g.game_date = '2006-01-22' AND b.team_id = 1610612747",
        "anchor_html",
        "team_box_scores/2006_01_22/200601220LAL.html",
        "https://www.basketball-reference.com/boxscores/200601220LAL.html",
        "",
    ),
    (
        "wilt_100_game",
        "game_boxscore",
        "chambwi01",
        1962,
        False,
        0,
        "PTS",
        100,
        "SELECT DISTINCT b.points FROM unified_star.fact_player_game_boxscore b "
        "JOIN api.v_game_summary g ON b.game_id = g.game_id "
        "WHERE b.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND g.game_date = '1962-03-02' "
        "  AND b.team_id = (SELECT DISTINCT team_id "
        "        FROM unified_star.fact_player_game_boxscore "
        "        WHERE player_id = (SELECT player_id FROM unified_star.dim_player "
        "                WHERE bref_player_id = 'chambwi01') "
        "          AND game_id IN (SELECT game_id FROM api.v_game_summary "
        "                  WHERE game_date = '1962-03-02') LIMIT 1)",
        "anchor_html",
        "team_box_scores/1962_03_02/196203020NYK.html",
        "https://www.basketball-reference.com/boxscores/196203020NYK.html",
        "pre_1973_boxscore_secondary_stats_zero_not_null_in_db",
    ),
    (
        "wilt_100_game_3p_zero",
        "game_boxscore",
        "chambwi01",
        1962,
        False,
        0,
        "FG3M",
        0,
        "SELECT DISTINCT b.fg3m FROM unified_star.fact_player_game_boxscore b "
        "JOIN api.v_game_summary g ON b.game_id = g.game_id "
        "WHERE b.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND g.game_date = '1962-03-02'",
        "anchor_html",
        "team_box_scores/1962_03_02/196203020NYK.html",
        "https://www.basketball-reference.com/boxscores/196203020NYK.html",
        "pre_1973_boxscore_3p_zero_in_db_not_null",
    ),
    (
        "wilt_100_game_stl_zero",
        "game_boxscore",
        "chambwi01",
        1962,
        False,
        0,
        "STL",
        0,
        "SELECT DISTINCT b.steals FROM unified_star.fact_player_game_boxscore b "
        "JOIN api.v_game_summary g ON b.game_id = g.game_id "
        "WHERE b.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND g.game_date = '1962-03-02'",
        "anchor_html",
        "team_box_scores/1962_03_02/196203020NYK.html",
        "https://www.basketball-reference.com/boxscores/196203020NYK.html",
        "pre_1973_boxscore_stl_zero_in_db_not_null",
    ),
    (
        "wilt_100_game_blk_zero",
        "game_boxscore",
        "chambwi01",
        1962,
        False,
        0,
        "BLK",
        0,
        "SELECT DISTINCT b.blocks FROM unified_star.fact_player_game_boxscore b "
        "JOIN api.v_game_summary g ON b.game_id = g.game_id "
        "WHERE b.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND g.game_date = '1962-03-02'",
        "anchor_html",
        "team_box_scores/1962_03_02/196203020NYK.html",
        "https://www.basketball-reference.com/boxscores/196203020NYK.html",
        "pre_1973_boxscore_blk_zero_in_db_not_null",
    ),
    (
        "bulls_1996_wins",
        "team_season",
        "CHI",
        1996,
        "",
        1610612741,
        "W",
        72,
        "SELECT s.w FROM unified_star.fact_team_season_summary s "
        "WHERE s.season_year = '1995-96' AND s.team_id = 1610612741",
        "anchor_html",
        "team_roster/CHI_1996.html",
        "https://www.basketball-reference.com/teams/CHI/1996.html",
        "immutable_record_72_wins",
    ),
    (
        "warriors_2016_wins",
        "team_season",
        "GSW",
        2016,
        "",
        1610612744,
        "W",
        73,
        "SELECT s.w FROM unified_star.fact_team_season_summary s "
        "WHERE s.season_year = '2015-16' AND s.team_id = 1610612744",
        "anchor_html",
        "team_roster/GSW_2016.html",
        "https://www.basketball-reference.com/teams/GSW/2016.html",
        "immutable_record_73_wins",
    ),
    (
        "lakers_1972_wins",
        "team_season",
        "LAL",
        1972,
        "",
        1610612747,
        "W",
        69,
        "SELECT s.w FROM unified_star.fact_team_season_summary s "
        "WHERE s.season_year = '1971-72' AND s.team_id = 1610612747",
        "anchor_html",
        "team_roster/LAL_1972.html",
        "https://www.basketball-reference.com/teams/LAL/1972.html",
        "immutable_record_69_wins",
    ),
    (
        "celtics_2024_wins",
        "team_season",
        "BOS",
        2024,
        "",
        1610612738,
        "W",
        64,
        "SELECT s.w FROM unified_star.fact_team_season_summary s "
        "WHERE s.season_year = '2023-24' AND s.team_id = 1610612738",
        "anchor_html",
        "team_roster/BOS_2024.html",
        "https://www.basketball-reference.com/teams/BOS/2024.html",
        "settled_2023_24_season",
    ),
    (
        "kareem_career_pts",
        "player_career",
        "abdulka01",
        "",
        "",
        0,
        "PTS",
        38387,
        "SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'abdulka01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/abdulka01.html",
        "https://www.basketball-reference.com/players/a/abdulka01.html",
        "",
    ),
    (
        "wilt_career_pts",
        "player_career",
        "chambwi01",
        "",
        "",
        0,
        "PTS",
        31419,
        "SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'chambwi01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/chambwi01.html",
        "https://www.basketball-reference.com/players/c/chambwi01.html",
        "",
    ),
    (
        "jordan_career_pts",
        "player_career",
        "jordami01",
        "",
        "",
        0,
        "PTS",
        32292,
        "SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'jordami01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/jordami01.html",
        "https://www.basketball-reference.com/players/j/jordami01.html",
        "",
    ),
    (
        "stockton_career_ast",
        "player_career",
        "stockjo01",
        "",
        "",
        0,
        "AST",
        15806,
        "SELECT SUM(s.ast) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'stockjo01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/stockjo01.html",
        "https://www.basketball-reference.com/players/s/stockjo01.html",
        "",
    ),
    (
        "stockton_career_stl",
        "player_career",
        "stockjo01",
        "",
        "",
        0,
        "STL",
        3265,
        "SELECT SUM(s.stl) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'stockjo01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/stockjo01.html",
        "https://www.basketball-reference.com/players/s/stockjo01.html",
        "",
    ),
    (
        "lebron_2024_pts",
        "player_season",
        "jamesle01",
        2024,
        False,
        1610612747,
        "PTS",
        1822,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'jamesle01') "
        "  AND s.season_year = '2024' AND s.is_playoffs = false "
        "  AND s.team_id = 1610612747",
        "anchor_html",
        "player_career_stats/jamesle01.html",
        "https://www.basketball-reference.com/players/j/jamesle01.html",
        "settled_2023_24_season",
    ),
    (
        "lebron_2024_po_pts",
        "player_season",
        "jamesle01",
        2024,
        True,
        1610612747,
        "PTS",
        139,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'jamesle01') "
        "  AND s.season_year = '2024' AND s.is_playoffs = true "
        "  AND s.team_id = 1610612747",
        "anchor_html",
        "player_career_stats/jamesle01.html",
        "https://www.basketball-reference.com/players/j/jamesle01.html",
        "settled_2023_24_playoffs",
    ),
    (
        "tatum_2024_reg_pts",
        "player_season",
        "tatumja01",
        2024,
        False,
        1610612738,
        "PTS",
        1987,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'tatumja01') "
        "  AND s.season_year = '2024' AND s.is_playoffs = false "
        "  AND s.team_id = 1610612738",
        "anchor_html",
        "player_career_stats/tatumja01.html",
        "https://www.basketball-reference.com/players/t/tatumja01.html",
        "settled_2023_24_season",
    ),
    (
        "tatum_2024_po_pts",
        "player_season",
        "tatumja01",
        2024,
        True,
        1610612738,
        "PTS",
        475,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'tatumja01') "
        "  AND s.season_year = '2024' AND s.is_playoffs = true "
        "  AND s.team_id = 1610612738",
        "anchor_html",
        "player_career_stats/tatumja01.html",
        "https://www.basketball-reference.com/players/t/tatumja01.html",
        "settled_2023_24_playoffs",
    ),
    (
        "luka_2024_pts_leader",
        "player_season",
        "doncilu01",
        2024,
        False,
        1610612742,
        "PTS",
        2370,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'doncilu01') "
        "  AND s.season_year = '2024' AND s.is_playoffs = false "
        "  AND s.team_id = 1610612742",
        "anchor_html",
        "player_career_stats/doncilu01.html",
        "https://www.basketball-reference.com/players/d/doncilu01.html",
        "season_pts_leader_2023_24",
    ),
    (
        "hali_2024_ast_leader",
        "player_season",
        "halibty01",
        2024,
        False,
        1610612754,
        "AST",
        752,
        "SELECT AST FROM api.v_canonical_player_season_totals "
        "WHERE PLAYER_ID = 'halibty01' AND SEASON = 2024 AND TEAM_ABBR = 'IND'",
        "anchor_html",
        "player_career_stats/halibty01.html",
        "https://www.basketball-reference.com/players/h/halibty01.html",
        "season_ast_leader_2023_24",
    ),
    (
        "sabonis_2024_reb_leader",
        "player_season",
        "sabondo01",
        2024,
        False,
        1610612758,
        "REB",
        1120,
        "SELECT TRB FROM api.v_canonical_player_season_totals "
        "WHERE PLAYER_ID = 'sabondo01' AND SEASON = 2024 AND TEAM_ABBR = 'SAC'",
        "anchor_html",
        "player_career_stats/sabondo01.html",
        "https://www.basketball-reference.com/players/s/sabondo01.html",
        "season_reb_leader_2023_24",
    ),
    (
        "wemby_2024_blk_leader",
        "player_season",
        "wembavi01",
        2024,
        False,
        1610612759,
        "BLK",
        254,
        "SELECT BLK FROM api.v_canonical_player_season_totals "
        "WHERE PLAYER_ID = 'wembavi01' AND SEASON = 2024 AND TEAM_ABBR = 'SAS'",
        "anchor_html",
        "player_career_stats/wembavi01.html",
        "https://www.basketball-reference.com/players/w/wembavi01.html",
        "season_blk_leader_2023_24",
    ),
    (
        "curry_2024_3pm_leader",
        "player_season",
        "curryst01",
        2024,
        False,
        1610612744,
        "3P",
        357,
        'SELECT "3P" FROM api.v_canonical_player_season_totals '
        "WHERE PLAYER_ID = 'curryst01' AND SEASON = 2024 AND TEAM_ABBR = 'GSW'",
        "anchor_html",
        "player_career_stats/curryst01.html",
        "https://www.basketball-reference.com/players/c/curryst01.html",
        "season_3pm_leader_2023_24",
    ),
    (
        "jokic_2024_per",
        "player_season",
        "jokicni01",
        2024,
        False,
        1610612743,
        "PER",
        31.0,
        "SELECT s.per FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'jokicni01') "
        "  AND s.season_year = '2024' AND s.is_playoffs = false "
        "  AND s.team_id = 1610612743",
        "anchor_html",
        "players_advanced_season_totals/2024_false.html",
        "https://www.basketball-reference.com/leagues/NBA_2024_advanced.html",
        "advanced_leader_2023_24",
    ),
    (
        "jokic_2024_bpm",
        "player_season",
        "jokicni01",
        2024,
        False,
        1610612743,
        "BPM",
        13.2,
        "SELECT s.bpm FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'jokicni01') "
        "  AND s.season_year = '2024' AND s.is_playoffs = false "
        "  AND s.team_id = 1610612743",
        "anchor_html",
        "players_advanced_season_totals/2024_false.html",
        "https://www.basketball-reference.com/leagues/NBA_2024_advanced.html",
        "advanced_leader_2023_24",
    ),
    (
        "harden_2022_brk_pts",
        "player_season",
        "hardeja01",
        2022,
        False,
        1610612751,
        "PTS",
        990,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'hardeja01') "
        "  AND s.season_year = '2022' AND s.is_playoffs = false "
        "  AND s.team_id = 1610612751",
        "live_url",
        "",
        "",
        "trade_split_invariant_brooklyn_stint",
    ),
    (
        "harden_2022_phi_pts",
        "player_season",
        "hardeja01",
        2022,
        False,
        1610612755,
        "PTS",
        442,
        "SELECT s.pts FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'hardeja01') "
        "  AND s.season_year = '2022' AND s.is_playoffs = false "
        "  AND s.team_id = 1610612755",
        "live_url",
        "",
        "",
        "trade_split_invariant_philadelphia_stint",
    ),
    (
        "harden_2022_combined_check",
        "player_season",
        "hardeja01",
        2022,
        False,
        0,
        "PTS",
        1432,
        "SELECT (SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "         WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "                 WHERE bref_player_id = 'hardeja01') "
        "           AND s.season_year = '2022' AND s.is_playoffs = false "
        "           AND s.team_id = 1610612751) "
        "     + (SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "         WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "                 WHERE bref_player_id = 'hardeja01') "
        "           AND s.season_year = '2022' AND s.is_playoffs = false "
        "           AND s.team_id = 1610612755)",
        "live_url",
        "",
        "",
        "trade_split_invariant_brk_plus_phi_equals_combined",
    ),
    (
        "lebron_career_pts",
        "player_career",
        "jamesle01",
        "",
        "",
        0,
        "PTS",
        43440,
        "SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'jamesle01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "live_url",
        "",
        "",
        "active_player_retrieval_2026_06_28",
    ),
    (
        "curry_career_3pm",
        "player_career",
        "curryst01",
        "",
        "",
        0,
        "3P",
        4248,
        'SELECT SUM("3P") FROM api.v_canonical_player_season_totals '
        "WHERE PLAYER_ID = 'curryst01'",
        "live_url",
        "",
        "",
        "active_player_retrieval_2026_06_28",
    ),
    (
        "bird_career_pts",
        "player_career",
        "birdla01",
        "",
        "",
        0,
        "PTS",
        21791,
        "SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'birdla01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/birdla01.html",
        "https://www.basketball-reference.com/players/b/birdla01.html",
        "",
    ),
    (
        "duncan_career_pts",
        "player_career",
        "duncati01",
        "",
        "",
        0,
        "PTS",
        26496,
        "SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'duncati01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "live_url",
        "",
        "",
        "promoted_from_surplus_anchor_only_no_html_in_corpus_yet",
    ),
    (
        "russell_career_pts",
        "player_career",
        "russebi01",
        "",
        "",
        0,
        "PTS",
        14522,
        "SELECT SUM(s.pts) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'russebi01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/russebi01.html",
        "https://www.basketball-reference.com/players/r/russebi01.html",
        "pre_1973_stl_and_blk_also_asserted_null",
    ),
    (
        "russell_career_stl_null",
        "player_career",
        "russebi01",
        "",
        "",
        0,
        "STL",
        None,
        "SELECT SUM(s.stl) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'russebi01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/russebi01.html",
        "https://www.basketball-reference.com/players/r/russebi01.html",
        "pre_1973_assert_null",
    ),
    (
        "russell_career_blk_null",
        "player_career",
        "russebi01",
        "",
        "",
        0,
        "BLK",
        None,
        "SELECT SUM(s.blk) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'russebi01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "anchor_html",
        "player_career_stats/russebi01.html",
        "https://www.basketball-reference.com/players/r/russebi01.html",
        "pre_1973_assert_null",
    ),
    (
        "paul_career_ast",
        "player_career",
        "paulch01",
        "",
        "",
        0,
        "AST",
        12552,
        "SELECT SUM(s.ast) FROM unified_star.fact_player_season_stats s "
        "WHERE s.player_id = (SELECT player_id FROM unified_star.dim_player "
        "        WHERE bref_player_id = 'paulch01') "
        "  AND s.season_year LIKE '%-%' AND s.is_playoffs = false",
        "live_url",
        "",
        "",
        "active_player_retrieval_2026_06_28",
    ),
    (
        "lal_franchise_pts_leader",
        "franchise_leaders",
        "LAL",
        "",
        "",
        1610612747,
        "PTS",
        33643,
        "SELECT MAX(reg_pts) FROM ("
        "  SELECT s.player_id, SUM(s.pts) AS reg_pts "
        "  FROM unified_star.fact_player_season_stats s "
        "  WHERE s.team_id = 1610612747 AND s.is_playoffs = false "
        "    AND s.season_year NOT LIKE '%-%' "
        "  GROUP BY s.player_id"
        ")",
        "anchor_html",
        "franchise_career_leaders/LAL_career_leaders.html",
        "https://www.basketball-reference.com/teams/LAL/leaders_career.html",
        "v_franchise_leaders_returns_38108_including_playoffs_filtering_required",
    ),
    (
        "bos_franchise_pts_leader",
        "franchise_leaders",
        "BOS",
        "",
        "",
        1610612738,
        "PTS",
        26395,
        "SELECT MAX(reg_pts) FROM ("
        "  SELECT s.player_id, SUM(s.pts) AS reg_pts "
        "  FROM unified_star.fact_player_season_stats s "
        "  WHERE s.team_id = 1610612738 AND s.is_playoffs = false "
        "    AND s.season_year NOT LIKE '%-%' "
        "  GROUP BY s.player_id"
        ")",
        "anchor_html",
        "franchise_career_leaders/BOS_career_leaders.html",
        "https://www.basketball-reference.com/teams/BOS/leaders_career.html",
        "v_franchise_leaders_returns_30168_including_playoffs_filtering_required",
    ),
]

COLUMNS = [
    "golden_id",
    "entity_type",
    "identifier",
    "season_end_year",
    "is_playoffs",
    "team_id",
    "stat_key",
    "expected_value",
    "sql_query",
    "source_type",
    "anchor_path",
    "source_url",
    "caveat",
]


def main() -> int:
    out = HERE / "golden.csv"
    # csv.QUOTE_MINIMAL ensures only fields with commas/quotes/newlines get quoted
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(COLUMNS)
        for r in ROWS:
            # Convert booleans to strings, None to empty
            row = []
            for v in r:
                if v is None:
                    row.append("")
                elif isinstance(v, bool):
                    row.append("true" if v else "false")
                else:
                    row.append(v)
            w.writerow(row)
    print(f"wrote {out} — {len(ROWS)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
