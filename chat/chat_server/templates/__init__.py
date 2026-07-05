"""Template registry package.

Importing this package triggers the loader (`_loader.py`), which scans
every capability subfolder, validates each template's SQL, and
populates the module-level `REGISTRY`. A template that fails
validation raises `RuntimeError` at import time (fail-fast per PLAN §7.3
and §14.1).

Public re-exports:
* `Template` — the registry dataclass.
* `REGISTRY` — the populated `dict[str, Template]`.
* `get_registry`, `get_template`, `list_templates`, `TemplateNotFound`.
"""

from chat_server.templates import _loader  # noqa: F401  — side-effect import populates REGISTRY.
from chat_server.templates._registry import (
    REGISTRY,
    Template,
    TemplateNotFound,
    get_registry,
    get_template,
    list_templates,
)

__all__ = [
    "REGISTRY",
    "Template",
    "TemplateNotFound",
    "get_registry",
    "get_template",
    "list_templates",
]
