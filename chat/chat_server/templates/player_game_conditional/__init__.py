"""``player_game_conditional`` template family (PLAN §11).

Covers per-game conditional aggregations: win/loss margin splits, milestone
ages (youngest triple-double), rare stat lines (quadruple-doubles), blocked-
shot streaks, and career-conditional totals (assists in scoreless games).
The grain is ``fact_player_game_box`` joined to ``fact_game_result`` (when a
margin/split is needed) and ``dim_player`` (when a name or birth date is
needed).
"""
