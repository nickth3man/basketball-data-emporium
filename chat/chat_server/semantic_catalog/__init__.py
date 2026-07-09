"""Semantic catalog for the governed-SQL chat migration.

This package defines the Pydantic schema for the business-model YAML files
that author every analytics question the chatbot is allowed to answer, plus
the loader that turns the on-disk YAML into a validated in-memory catalog.

Public surface
--------------
* :class:`BusinessModel`, :class:`Dimension`, :class:`Measure`,
  :class:`Join`, :class:`Caveat` -- Pydantic models describing the YAML
  shape (loosely modeled on ``boring-semantic-layer``'s ``flights.yml``,
  but hand-rolled in-house so we own the schema and avoid adding a
  semantic-layer framework dependency).
* :class:`SemanticCatalog` -- a thin in-memory container with
  :meth:`list_models` and :meth:`get_model` helpers.
* :func:`load_catalog` -- module-level cached loader that reads every
  ``*.yml`` under this package's ``models/`` subdirectory, validates
  join targets resolve, and returns the populated :class:`SemanticCatalog`.

Phase coverage
-------------
This package delivers the catalog schema + reference YAML.
It is the dependency root for the remaining YAML models,
the schema-retrieval layer, and the SQL validation gate.
"""

from __future__ import annotations

from .loader import SemanticCatalog, load_catalog, reset_catalog_cache
from .schema import BaseTable, BusinessModel, Caveat, Dimension, Join, Measure

__all__ = [
    "BaseTable",
    "BusinessModel",
    "Caveat",
    "Dimension",
    "Join",
    "Measure",
    "SemanticCatalog",
    "load_catalog",
    "reset_catalog_cache",
]
