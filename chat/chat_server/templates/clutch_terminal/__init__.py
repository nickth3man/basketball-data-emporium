"""``clutch_terminal`` template family (PLAN §11).

Clutch window and terminal-possession queries derived from
``fact_pbp_event``. Per PLAN §11.1 the canonical clutch mart
(``agg_clutch_stats``) is an ``empty_endpoint_shell``, so these templates
compute clutch metrics live from play-by-play with a bounded
``season_year`` + ``season_type`` filter.
"""
