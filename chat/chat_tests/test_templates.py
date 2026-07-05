"""Integration tests for the template registry.

Mirrors the ``web/test/fixtures/`` pattern from the Express app: each
template ships with a ``TESTS`` list (in its metadata module) and the
test below runs each spec against the live warehouse. Tests skip
cleanly when ``data/nba.duckdb`` is absent (see conftest.skip_no_db).
"""

from __future__ import annotations

import pytest

from chat_server.db import get_db
from chat_server.templates import TemplateNotFound, get_registry, get_template
from chat_tests.conftest import skip_no_db


@pytest.fixture(scope="module")
def registry() -> dict:
    """Module-scoped registry snapshot.

    Loading happens once per test module via the import of
    ``chat_server.templates``. The fixture just exposes it.
    """
    return get_registry()


@skip_no_db
def test_registry_has_fifty_forty_ninety(registry: dict) -> None:
    """Phase 1 ships the 50-40-90 template."""
    assert "season_thresholds.fifty_forty_ninety" in registry


@skip_no_db
def test_all_templates_validate(registry: dict) -> None:
    """Every registered template passed ``validate_template_sql`` at load time.

    The loader raises at import if any template fails validation, so the
    act of getting here means every template is valid; this test just
    pins that the registry is non-empty (and therefore the loader ran).
    """
    assert len(registry) >= 1


@skip_no_db
def test_get_template_unknown_id_raises() -> None:
    """Unknown ids raise ``TemplateNotFound``."""
    with pytest.raises(TemplateNotFound):
        get_template("does.not.exist")


@skip_no_db
async def test_fifty_forty_ninety_loop() -> None:
    """Run every entry in ``TESTS`` against the live warehouse.

    A single test that loops over ``tmpl.tests`` keeps collection simple
    — the registry is guaranteed loaded by the time the test body runs,
    regardless of pytest's collection order quirks. Each entry's
    ``params`` is validated through the template's Pydantic model before
    being bound to the SQL.
    """
    tmpl = get_template("season_thresholds.fifty_forty_ninety")
    db = get_db()

    for idx, spec in enumerate(tmpl.tests):
        params = tmpl.params_model(**spec["params"])
        result = await db.execute(tmpl.sql, params.model_dump())

        min_rows = spec.get("expect_min_rows", 1)
        assert result.row_count >= min_rows, (
            f"test[{idx}] expected >={min_rows} rows, got {result.row_count}"
        )

        if "expect_contains_player" in spec:
            names = [row["full_name"] for row in result.rows]
            expected = spec["expect_contains_player"]
            assert expected in names, f"test[{idx}] expected {expected!r} in {names}"
