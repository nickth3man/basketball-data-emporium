"""``clutch_terminal`` template family.

Clutch window and terminal-possession queries derived from
``fact_pbp_event``. The canonical clutch mart
(``agg_clutch_stats``) is an ``empty_endpoint_shell``, so these templates
compute clutch metrics live from play-by-play with a bounded
``season_year`` + ``season_type`` filter.
"""
