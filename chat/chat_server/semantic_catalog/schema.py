"""Pydantic models for the semantic-catalog YAML format (PLAN §1 Phase 1).

The schema is hand-rolled in-house. It borrows the SHAPE of
``boring-semantic-layer``'s ``flights.yml`` (per-field ``description``,
explicit ``joins:`` with ``type`` / ``left_on`` / ``right_on``) but does
NOT introduce any new pip dependency. Every model carries
``model_config = ConfigDict(extra="forbid")`` so an unrecognized key in a
YAML file fails at load time rather than silently being ignored.

Catalog grain
-------------
Each :class:`BusinessModel` represents one analytical concept the chatbot
may answer (e.g. ``player_career``). Its :class:`BaseTable` is the real
warehouse table the SQL agent is allowed to ``FROM`` (the
``ALLOWED_TABLES_FOR_AGENT`` allowlist in :mod:`chat_server.schema_context`
is the cross-check). :class:`Dimension` values are scalar expressions over
the base-table alias (e.g. ``ps.player_id``); :class:`Measure` values are
aggregates (e.g. ``SUM(ps.total_pts)``). :class:`Join` targets must be
other catalog models -- the loader validates every reference at load time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """Base model: reject unknown YAML keys so typos fail loudly at load time."""

    model_config = ConfigDict(extra="forbid")


class BaseTable(_StrictModel):
    """The real warehouse table backing a :class:`BusinessModel`.

    Attributes
    ----------
    name
        The actual DuckDB table name (must exist in
        ``information_schema.tables`` and be a member of the agent's
        allowlist; the catalog loader does not enforce this -- the
        grounding test does).
    alias
        Short alias used as the SQL ``FROM`` prefix (e.g. ``ps`` for
        ``mart_player_season``). Keep it short; it prefixes every
        dimension / measure expression in the same model.
    """

    name: str = Field(..., min_length=1)
    alias: str = Field(..., min_length=1)


class Dimension(_StrictModel):
    """A scalar (non-aggregated) field exposed by a business model.

    ``expr`` is written against the :class:`BaseTable.alias` (e.g.
    ``ps.player_id``, ``ps.season_year``). It must be a single-column or
    simple-cast expression -- not an aggregate.
    """

    name: str = Field(..., min_length=1)
    expr: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)


#: Per the ``boring-semantic-layer`` cube recipes referenced in PLAN §6:
#: "sum" = additive across the grain (e.g. games, points, rebounds via
#: SUM); "non_additive" = per-row average or ratio (e.g. PPG, FG%) where
#: re-summing across grains double-counts; "count_distinct" = distinct
#: count (sums correctly but loses duplicates, hence its own flag);
#: "percentile" = requires ordering, never sums.
Additivity = Literal["sum", "non_additive", "count_distinct", "percentile"]


class Measure(_StrictModel):
    """An aggregated metric exposed by a business model.

    ``expr`` is an aggregate over the base-table alias (e.g.
    ``SUM(ps.total_pts)``, ``AVG(ps.avg_pts)``).
    """

    name: str = Field(..., min_length=1)
    expr: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    additivity: Additivity = "sum"


#: Cardinality from the SOURCE model's perspective: "one_to_one" = each
#: source row joins to at most one target row; "many_to_one" = many source
#: rows share one target row (typical lookup); "one_to_many" = one source
#: row fans out to many target rows (drill-down).
JoinType = Literal["one_to_one", "many_to_one", "one_to_many"]


class Join(_StrictModel):
    """A cross-model join path declared by a :class:`BusinessModel`.

    The loader enforces that ``model`` resolves to another business model
    in the catalog -- unresolved references raise at load time so the
    agent never tries to generate SQL against a phantom model.
    """

    name: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    type: JoinType
    left_on: str = Field(..., min_length=1)
    right_on: str = Field(..., min_length=1)
    description: str | None = None


class Caveat(_StrictModel):
    """A single free-text caveat attached to a business model.

    Caveats are surfaced to the agent alongside the model so it can fold
    them into the answer composer when relevant. They mirror the spirit
    of the ``meta_known_gap`` table but are hand-authored per business
    concept rather than per warehouse table.
    """

    text: str = Field(..., min_length=1)


class BusinessModel(_StrictModel):
    """A governed business model the chatbot is allowed to answer against.

    Each instance corresponds to one YAML file under
    ``chat_server/semantic_catalog/models/``. The loader enforces unique
    ``model`` names across the catalog and that every :class:`Join.model`
    reference resolves to a peer model. The grounding test
    (``chat_tests/test_semantic_catalog.py``) additionally asserts that
    :attr:`base_table.name` exists in the live warehouse.
    """

    model: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    grain: str = Field(..., min_length=1)
    base_table: BaseTable
    dimensions: list[Dimension] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    joins: list[Join] = Field(default_factory=list)
    # Free-text caveats, mirroring the spirit of `meta_known_gap`. Kept as
    # plain strings per the PLAN §1 spec; the standalone :class:`Caveat`
    # Pydantic model is exported for callers that want a structured shape.
    caveats: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    example_questions: list[str] = Field(default_factory=list)


__all__ = [
    "Additivity",
    "BaseTable",
    "BusinessModel",
    "Caveat",
    "Dimension",
    "Join",
    "JoinType",
    "Measure",
]
