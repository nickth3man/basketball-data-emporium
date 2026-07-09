"""``career_demographic`` template family.

Country + 500 GP, career scoring, and draft value queries. All templates in
this folder query canonical marts (``mart_player_career``, ``mart_draft_value``)
plus ``dim_player`` plus selected source-backed tables
(``fact_draft``, ``src_agg_player_season_advanced`` for win shares).
"""
