"""Public API surface guardrails.

These constants name warehouse objects that are intentionally deferred from
generic dataset exposure until they have dedicated query contracts and UI.
"""

from __future__ import annotations

DEFERRED_HEAVY_API_OBJECTS: frozenset[str] = frozenset(
    {
        "v_player_game_log",
        "v_shot_chart",
    }
)
