"""Tests for the semantic catalog package.

Five tests cover the loader's contract end to end:

* ``test_load_catalog_returns_all_models`` -- the 8 expected business
  models are all parsed and addressable.
* ``test_player_career_model_grounding`` -- the fully-grounded reference
  model points at a real warehouse table (skipped when the warehouse is
  absent, mirroring the existing ``skip_no_db`` helper in conftest.py).
* ``test_joins_resolve`` -- every cross-model Join resolves to a peer
  model in the catalog.
* ``test_no_duplicate_model_names`` -- the loader raises when two YAML
  files declare the same ``model`` name.
* ``test_measures_have_additivity`` -- every measure carries a valid
  additivity value from the closed enum.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from chat_server.semantic_catalog import (
    BusinessModel,
    load_catalog,
    reset_catalog_cache,
)
from chat_tests.conftest import skip_no_db

EXPECTED_MODELS: frozenset[str] = frozenset(
    {
        "player_career",
        "player_season",
        "team_season",
        "games",
        "awards",
        "standings",
        "shots",
        "head_to_head",
        "draft",
        "player_game_box",
    }
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Reset the module-level catalog cache around every test.

    Some tests intentionally mutate state (e.g. duplicate model names),
    so each test must start with a clean cache to avoid bleed-through.
    """
    reset_catalog_cache()
    yield
    reset_catalog_cache()


def test_load_catalog_returns_all_models() -> None:
    """``load_catalog()`` returns exactly the 8 expected model names."""
    catalog = load_catalog()
    assert set(catalog.list_models()) == EXPECTED_MODELS


@skip_no_db
def test_player_career_model_grounding() -> None:
    """``player_career`` points at a real warehouse table.

    Uses the same ``duckdb`` import pattern as
    ``chat_server.schema_context`` so this test stays independent of the
    server-side async singleton. The ``skip_no_db`` marker (defined in
    ``chat_tests/conftest.py``) skips when the warehouse file is
    absent, mirroring every other DB-backed test in this package.
    """
    import duckdb

    from chat_server.config import get_settings

    db_path = get_settings().duckdb_path
    pc = load_catalog().get_model("player_career")
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = ? LIMIT 1",
            [pc.base_table.name],
        ).fetchall()
    finally:
        con.close()
    assert rows, (
        f"player_career.base_table.name={pc.base_table.name!r} is not present "
        "in the warehouse's information_schema.tables"
    )


def test_joins_resolve() -> None:
    """Every ``Join.model`` across every model resolves to a catalog peer."""
    catalog = load_catalog()
    names = set(catalog.list_models())
    unresolved: list[str] = []
    for m in catalog.list_models():
        bm: BusinessModel = catalog.get_model(m)
        for join in bm.joins:
            if join.model not in names:
                unresolved.append(f"{m}.joins[{join.name!r}] -> {join.model!r}")
    assert not unresolved, "unresolved join targets: " + ", ".join(unresolved)


def test_no_duplicate_model_names(tmp_path: Path) -> None:
    """Loader raises when two YAML files declare the same ``model`` name."""
    # Re-use the package's first YAML as the legitimate copy, then add a
    # duplicate file with the same `model:` name but a different
    # description so Pydantic doesn't reject it before the dup check.
    pkg_dir = Path(__file__).resolve().parents[1] / "chat_server" / "semantic_catalog"
    src_files = sorted((pkg_dir / "models").glob("*.yml"))
    assert src_files, "expected the package to ship with at least one YAML model"
    (tmp_path / "first.yml").write_text(src_files[0].read_text(encoding="utf-8"))
    duplicate_body = (
        src_files[0]
        .read_text(encoding="utf-8")
        .replace(
            "description:",
            "description: DUPLICATE ",
            1,
        )
    )
    (tmp_path / "second.yml").write_text(duplicate_body, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate model names"):
        load_catalog(models_dir=tmp_path)


def test_measures_have_additivity() -> None:
    """Every measure's additivity is one of the four valid literals."""
    catalog = load_catalog()
    valid = {"sum", "non_additive", "count_distinct", "percentile"}
    bad: list[str] = []
    for m in catalog.list_models():
        bm = catalog.get_model(m)
        for measure in bm.measures:
            if measure.additivity not in valid:
                bad.append(f"{m}.{measure.name}={measure.additivity!r}")
    assert not bad, "measures with invalid additivity: " + ", ".join(bad)
