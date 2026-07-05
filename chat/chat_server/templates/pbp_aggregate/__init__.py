"""``pbp_aggregate`` template family (PLAN §11).

Heavy play-by-play aggregates that derive from ``fact_pbp_event``: scoring
runs across a game's event sequence, foul taxonomy aggregates per period,
etc. Bound every PBP scan with a ``season_year`` filter — one season is
~1M rows, which keeps these queries within the 300s hard timeout.
"""
