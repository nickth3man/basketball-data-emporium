"""``career_demographic`` template family.

PLAN §11: country + 500 GP, career scoring, draft value. All templates in
this folder query canonical marts (``mart_player_career``, ``mart_draft_value``)
plus ``dim_player`` plus selected source-backed tables
(``fact_draft``, ``src_agg_player_season_advanced`` for win shares).
"""
