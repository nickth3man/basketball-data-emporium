"""``lineup_court`` template family.

5-man shared-court lineup queries. The canonical lineup-roster map lives
in ``fact_lineup_player`` (one row per player-in-lineup-game, keyed by a
``team_id-game_id-season_year`` ``group_id``); per-game aggregate net
rating / minutes / wins are read from ``src_agg_lineup_efficiency``
(verified during Phase 6 — ``src_fact_lineup_stats`` is empty). 5-man
lineup identification is done via ``GROUP BY group_id HAVING
COUNT(DISTINCT player_id) = N``.
"""
