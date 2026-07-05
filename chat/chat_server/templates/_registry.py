"""Template registry: typed view + lookup helpers (PLAN §7.3).

A `Template` is the metadata the runner needs to execute a parameterized
SQL query against the warehouse. The registry is populated by the loader
(`_loader.py`) at import time; see `chat_server.templates.__init__` for
the entrypoint.

Public surface (re-exported from `chat_server.templates.__init__`):
* `Template` — the dataclass.
* `REGISTRY` — the populated dict (read-only contract; the loader writes).
* `get_registry()`, `get_template(id)`, `list_templates(capability=None)`.
* `TemplateNotFound` — raised by `get_template` for unknown ids.
"""

from __future__ import annotations

# `field` is re-exported in the public surface for future templates that may
# want mutable defaults via `field(default_factory=...)`; `BaseModel` is the
# documented base class of every `params_model` (kept here for type introspection).
from dataclasses import dataclass, field  # noqa: F401
from typing import Any

from pydantic import BaseModel  # noqa: F401

#: A template's `params_model` is a subclass of `pydantic.BaseModel`; we type
#: it as `type` here because the registry only needs to know "this is a class",
#: not its full Pydantic generic shape.
ParamsModel = type[BaseModel]


@dataclass
class Template:
    """One registered query template.

    Attributes
    ----------
    template_id
        Dotted identifier (e.g. ``"season_thresholds.fifty_forty_ninety"``).
        Acts as the registry key.
    title
        Short human-readable name shown in the template picker.
    description
        One-paragraph explanation of what the template answers.
    sql
        The SQL text with DuckDB ``$name`` placeholders. Validated at load
        time against `allowed_tables` (see `chat_server.validation`).
    params_model
        Pydantic model class used to validate and document parameters.
    allowed_tables
        Set of base table names the SQL is permitted to reference.
        Enforced by `validate_template_sql` at load time.
    result_schema
        Column-name -> Python type. Used by the composer for typed display.
    answer_policy
        Hint to the answer composer (e.g. ``"ranked_list"``).
    default_limit
        Default row cap applied when the SQL has no `LIMIT` clause.
    timeout_seconds
        Per-template hard timeout; runner enforces it.
    examples
        Natural-language questions the template answers well.
    tests
        Live-warehouse assertions used by the integration test suite.
        Each dict has at least a ``params`` key and may have
        ``expect_min_rows`` / ``expect_contains_player`` / etc.
    capability
        Folder name that groups the template (see PLAN §11).
    """

    template_id: str
    title: str
    description: str
    sql: str
    params_model: ParamsModel
    allowed_tables: set[str]
    result_schema: dict[str, type]
    answer_policy: str
    default_limit: int
    timeout_seconds: int
    examples: list[str]
    tests: list[dict[str, Any]]
    capability: str


class TemplateNotFound(LookupError):  # noqa: N818  — name fixed by the registry contract (see PLAN §7.3).
    """Raised by `get_template` when the requested id is not registered."""


#: The live registry, keyed by `template_id`. Populated by the loader at
#: import time. Treat as read-only after startup; the loader is the only
#: writer and it runs once.
REGISTRY: dict[str, Template] = {}


def get_registry() -> dict[str, Template]:
    """Return the live registry dict.

    The returned reference is the actual module-level dict (not a copy) so
    tests can introspect it. Callers must not mutate it.
    """
    return REGISTRY


def get_template(template_id: str) -> Template:
    """Look up a template by id.

    Raises
    ------
    TemplateNotFound
        If `template_id` is not present in the registry.
    """
    try:
        return REGISTRY[template_id]
    except KeyError as exc:
        raise TemplateNotFound(
            f"unknown template_id: {template_id!r}; known ids: {sorted(REGISTRY)}"
        ) from exc


def list_templates(capability: str | None = None) -> list[Template]:
    """Return all registered templates, optionally filtered by capability.

    Parameters
    ----------
    capability
        If provided, only templates whose `capability` matches are returned
        (matches the family folder name, e.g. ``"season_thresholds"``).
        If `None`, all templates are returned.

    Returns
    -------
    list[Template]
        Templates in stable, sorted-by-id order.
    """
    all_templates = sorted(REGISTRY.values(), key=lambda t: t.template_id)
    if capability is None:
        return all_templates
    return [t for t in all_templates if t.capability == capability]


__all__ = [
    "Template",
    "TemplateNotFound",
    "REGISTRY",
    "get_registry",
    "get_template",
    "list_templates",
]
