"""File-based loader for the template registry (PLAN §7.3).

Walks the ``chat_server/templates/`` package, pairs each ``<stem>.sql``
file with its sibling ``<stem>.py`` metadata module, and registers the
resulting `Template` after running `validate_template_sql`.

Imported for side effects from `chat_server.templates.__init__` so the
registry is populated before any caller asks for it. A template that
fails validation raises at import time — fail-fast per PLAN §7.3 and §14.1.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from chat_server.validation import validate_template_sql

from ._registry import REGISTRY, Template

#: Templates ship in subfolders of `chat_server/templates/`, one per
#: analytical capability (see PLAN §11). A template's `capability` is set
#: from its parent folder name.
_PACKAGE_DIR = Path(__file__).resolve().parent

#: Required module-level constants on every template metadata module.
#: Optional ones (with defaults) are listed in `_DEFAULTS`.
_REQUIRED_CONSTANTS: tuple[str, ...] = (
    "TEMPLATE_ID",
    "TITLE",
    "DESCRIPTION",
    "ALLOWED_TABLES",
    "Params",
    "RESULT_SCHEMA",
    "ANSWER_POLICY",
    "DEFAULT_LIMIT",
    "EXAMPLES",
    "TESTS",
)


def _load_metadata_module(capability: str, stem: str, py_path: Path):
    """Import a `<stem>.py` template metadata module by file path.

    The module is registered under a synthetic name so it can be inspected
    via `sys.modules` later (useful for debugging in REPL sessions).
    """
    module_name = f"chat_server.templates.{capability}.{stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not build import spec for {py_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _read_required_constant(module: Any, name: str, py_path: Path) -> Any:
    """Return a required constant from `module`, raising a helpful error if missing.

    Returns ``Any`` (the caller annotates the result with the concrete
    type the metadata module is contracted to provide — `str`, `int`,
    `list[str]`, `type[BaseModel]`, etc.). This avoids a mismatch where
    a narrower return annotation here would be a lie (we genuinely don't
    know what kind of value each required constant holds).
    """
    if not hasattr(module, name):
        raise RuntimeError(
            f"{py_path}: missing required constant {name!r}; "
            f"every template must define {_REQUIRED_CONSTANTS}"
        )
    return getattr(module, name)


def _register_template(capability: str, sql_path: Path, py_path: Path) -> Template:
    """Build a `Template` from the SQL + sibling Python module, validate it, register it."""
    module = _load_metadata_module(capability, sql_path.stem, py_path)

    # Explicit annotations narrow `getattr(...)`-typed values from `object`
    # to the concrete shapes the `Template` dataclass expects; without
    # these, ty reports "Invalid subscript of object of type `set[_T@set]`"
    # / "found `object`" downstream.
    template_id: str = _read_required_constant(module, "TEMPLATE_ID", py_path)
    title: str = _read_required_constant(module, "TITLE", py_path)
    description: str = _read_required_constant(module, "DESCRIPTION", py_path)
    allowed_tables_raw: list[str] = _read_required_constant(module, "ALLOWED_TABLES", py_path)
    params_model: type[BaseModel] = _read_required_constant(module, "Params", py_path)
    result_schema_raw: dict[str, type] = _read_required_constant(module, "RESULT_SCHEMA", py_path)
    answer_policy: str = _read_required_constant(module, "ANSWER_POLICY", py_path)
    default_limit: int = _read_required_constant(module, "DEFAULT_LIMIT", py_path)
    examples_raw: list[str] = _read_required_constant(module, "EXAMPLES", py_path)
    tests_raw: list[dict[str, Any]] = _read_required_constant(module, "TESTS", py_path)

    # Optional fields with sane defaults (PLAN §13: heavy templates set 300).
    timeout_seconds: int = getattr(module, "TIMEOUT_SECONDS", 30)

    sql_text = sql_path.read_text(encoding="utf-8")

    # Validate the SQL against the allowlist before exposing the template.
    report = validate_template_sql(sql_text, set(allowed_tables_raw))
    if not report.valid:
        raise RuntimeError(
            f"template {template_id!r} failed validate_template_sql: {report.errors}; "
            f"tables_referenced={sorted(report.tables_referenced)}"
        )

    template = Template(
        template_id=template_id,
        title=title,
        description=description,
        sql=sql_text,
        params_model=params_model,
        allowed_tables=set(allowed_tables_raw),
        result_schema=dict(result_schema_raw),
        answer_policy=answer_policy,
        default_limit=default_limit,
        timeout_seconds=timeout_seconds,
        examples=list(examples_raw),
        tests=list(tests_raw),
        capability=capability,
    )

    if template.template_id in REGISTRY:
        raise RuntimeError(
            f"duplicate template_id {template.template_id!r} (also defined in another module)"
        )
    REGISTRY[template.template_id] = template
    return template


def _load_all() -> None:
    """Walk the package, registering every paired (`.sql`, `.py`) template."""
    # Skip non-template entries: this loader module itself + private files.
    for entry in sorted(_PACKAGE_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name.startswith("."):
            # Private packages (`_loader` lives in the package root, not here).
            continue
        capability = entry.name
        # Skip `__pycache__` etc.
        if not (entry / "__init__.py").exists():
            continue
        for sql_path in sorted(entry.glob("*.sql")):
            py_path = sql_path.with_suffix(".py")
            if not py_path.exists():
                raise RuntimeError(
                    f"template SQL {sql_path.name} has no sibling Python module "
                    f"(expected {py_path.name})"
                )
            _register_template(capability, sql_path, py_path)


# Run the load eagerly on import. The `noqa: F401` re-export in
# `chat_server.templates.__init__` ensures this module is always imported.
_load_all()
