"""Loader for the semantic-catalog YAML files (PLAN §1 Phase 1).

Reads every ``*.yml`` under this package's ``models/`` subdirectory,
parses each into a :class:`~chat_server.semantic_catalog.schema.BusinessModel`,
and returns a :class:`SemanticCatalog`. Validates that:

* model names are unique across the catalog (otherwise raises)
* every :class:`~chat_server.semantic_catalog.schema.Join.model` reference
  resolves to a peer model in the catalog (otherwise raises)

The result is cached at module scope so repeated ``load_catalog()`` calls
in a single process do not re-parse YAML. The cache is invalidated by
:func:`reset_catalog_cache` for tests.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import BusinessModel

# Default models directory: <this-package>/models/. Resolved relative to
# THIS file so it works regardless of the caller's current working
# directory (matters for FastAPI, pytest, and CLI invocation paths).
_DEFAULT_MODELS_DIR = Path(__file__).resolve().parent / "models"


class SemanticCatalog:
    """In-memory container for parsed business models.

    Behaves like a read-only mapping by name. The :meth:`list_models` and
    :meth:`get_model` helpers are the supported access patterns -- the
    underlying dict is exposed as :attr:`models` for callers that need
    direct iteration (e.g. the schema-retrieval embedder in Phase 3).
    """

    __slots__ = ("models",)

    def __init__(self, models: dict[str, BusinessModel]) -> None:
        self.models = models

    def list_models(self) -> list[str]:
        """Return every registered model name, sorted for determinism."""
        return sorted(self.models)

    def get_model(self, name: str) -> BusinessModel:
        """Return the named model. Raises :class:`KeyError` if unknown."""
        return self.models[name]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.models

    def __len__(self) -> int:
        return len(self.models)

    def __iter__(self):
        return iter(self.models)


def _parse_yaml_file(path: Path) -> BusinessModel:
    """Parse one YAML file into a :class:`BusinessModel`.

    Pydantic's ``extra='forbid'`` (set on the schema) raises a clear
    :class:`pydantic.ValidationError` if the YAML has an unrecognized
    key, which is what we want -- typos must surface at load time.
    """
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        raise ValueError(f"{path}: empty YAML file")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a top-level mapping, got {type(data).__name__}")
    return BusinessModel.model_validate(data)


def _resolve_joins(models: dict[str, BusinessModel]) -> None:
    """Validate every ``Join.model`` reference points to a known model.

    Raises :class:`ValueError` listing the first unresolved reference per
    offending model -- this is the strictness gate that keeps the agent
    from generating SQL against a phantom model.
    """
    unresolved: list[str] = []
    for m in models.values():
        for join in m.joins:
            if join.model not in models:
                unresolved.append(
                    f"{m.model}.joins[{join.name!r}]: target model {join.model!r} not in catalog"
                )
    if unresolved:
        raise ValueError(
            "semantic catalog has unresolved join references:\n  - " + "\n  - ".join(unresolved)
        )


def load_catalog(models_dir: Path | None = None) -> SemanticCatalog:
    """Load every ``*.yml`` under ``models_dir`` (default: package's models/).

    Returns a :class:`SemanticCatalog` keyed by ``model`` name. Cached at
    module scope so repeated calls in the same process do not re-parse.

    Raises
    ------
    ValueError
        If a YAML file is malformed, two files declare the same ``model``
        name, or a :class:`Join.model` reference fails to resolve.
    """
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    root = models_dir if models_dir is not None else _DEFAULT_MODELS_DIR
    if not root.is_dir():
        raise ValueError(f"semantic catalog models directory not found: {root}")

    files = sorted(p for p in root.iterdir() if p.suffix.lower() in {".yml", ".yaml"})
    if not files:
        raise ValueError(f"no YAML models found under {root}")

    models: dict[str, BusinessModel] = {}
    duplicates: list[str] = []
    for path in files:
        bm = _parse_yaml_file(path)
        if bm.model in models:
            duplicates.append(
                f"{bm.model!r} declared in both {path.name} and {_owner_of(models, bm.model)}"
            )
            continue
        models[bm.model] = bm
    if duplicates:
        raise ValueError(
            "semantic catalog has duplicate model names:\n  - " + "\n  - ".join(duplicates)
        )

    _resolve_joins(models)
    _catalog_cache = SemanticCatalog(models)
    return _catalog_cache


def _owner_of(models: dict[str, BusinessModel], name: str) -> str:
    """Return the YAML filename that first declared ``name`` (test helper)."""
    # Best-effort: the cache has not been populated yet so we don't know
    # the source filename at this point; fall back to the model name.
    return f"<previously parsed model {name!r}>"


def reset_catalog_cache() -> None:
    """Clear the module-level catalog cache. Test helper only."""
    global _catalog_cache
    _catalog_cache = None


# Module-level cache. Tests reset it via :func:`reset_catalog_cache`.
_catalog_cache: SemanticCatalog | None = None


__all__ = ["SemanticCatalog", "load_catalog", "reset_catalog_cache"]
