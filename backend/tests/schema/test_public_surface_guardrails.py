"""Guardrails for intentionally deferred high-cardinality public surfaces."""

from __future__ import annotations

from basketball_data_emporium.db.public_surface import DEFERRED_HEAVY_API_OBJECTS
from basketball_data_emporium.db.registry import DATASET_BINDINGS


def test_generic_dataset_registry_does_not_expose_deferred_heavy_views() -> None:
    exposed = {binding.sql_object for binding in DATASET_BINDINGS}
    assert DEFERRED_HEAVY_API_OBJECTS.isdisjoint(exposed), (
        "High-cardinality views need dedicated query contracts and UI before "
        f"public exposure; exposed={sorted(DEFERRED_HEAVY_API_OBJECTS & exposed)!r}."
    )


def test_public_dataset_bindings_are_bounded_and_ordered() -> None:
    for binding in DATASET_BINDINGS:
        assert binding.max_page_size <= 500, (
            f"{binding.dataset_id} has max_page_size={binding.max_page_size}; "
            "public datasets must stay bounded."
        )
        assert binding.default_order_by, (
            f"{binding.dataset_id} needs deterministic ordering before exposure."
        )
