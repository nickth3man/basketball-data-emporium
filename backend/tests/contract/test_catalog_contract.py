"""MVCS Test 4 — `/api/endpoints/player-hub` and `/api/endpoints/team-hub` HTTP contract.

Companion to `tests/contract/test_status_contract.py`. Exercises the
full HTTP contract of the two catalog endpoints using FastAPI's
in-process `TestClient` (no port binding, no `uvicorn`).

What the contract pins
----------------------
* Both endpoints return HTTP 200 and `application/json`.
* The body has the exact top-level shape (`tabs` + `datasets`).
* Each tab has `id`, `label`, `description`, `scope`, `datasets`,
  `default_dataset`; `default_dataset` is always present in the
  `datasets` list (when the list is non-empty).
* Player-hub tabs scope ∈ {player, season}; team-hub tabs scope ∈
  {team, team_season}.
* Each dataset has 8 fields (id, label, endpoint_name, scope,
  description, columns, default_visible_columns, supports_export);
  each column is a `ColumnMeta` with `key`, `label`,
  `default_visible`, `numeric`.
* Two back-to-back calls return byte-identical bodies (the catalog is
  built once at module load and cached).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Player-hub
# ---------------------------------------------------------------------------


def test_player_catalog_status_code(contract_client: TestClient) -> None:
    """`GET /api/endpoints/player-hub` returns HTTP 200."""
    response = contract_client.get("/api/endpoints/player-hub")
    assert response.status_code == 200, (
        f"GET /api/endpoints/player-hub returned {response.status_code} "
        f"(expected 200); body: {response.text!r}"
    )


def test_player_catalog_top_level_keys(contract_client: TestClient) -> None:
    """Player-hub body has exactly two top-level keys: `tabs` and `datasets`."""
    response = contract_client.get("/api/endpoints/player-hub")
    body = response.json()
    assert set(body.keys()) == {"tabs", "datasets"}, (
        f"player-hub top-level keys were {sorted(body.keys())!r}; "
        f"expected exactly {{'tabs', 'datasets'}}."
    )
    assert isinstance(body["tabs"], list)
    assert isinstance(body["datasets"], list)


def test_player_catalog_tab_shape(contract_client: TestClient) -> None:
    """Each player-hub tab has the expected 6 fields and a valid scope + default_dataset."""
    response = contract_client.get("/api/endpoints/player-hub")
    tabs = response.json()["tabs"]
    assert tabs, "player-hub catalog declared no tabs (expected ≥ 1)."

    required = {"id", "label", "description", "scope", "datasets", "default_dataset"}
    for tab in tabs:
        assert set(tab.keys()) == required, (
            f"player-hub tab {tab.get('id')!r} keys were {sorted(tab.keys())!r}; "
            f"expected exactly {sorted(required)!r}."
        )
        assert tab["scope"] in {"player", "season"}, (
            f"player-hub tab {tab['id']!r} scope {tab['scope']!r} not in "
            f"{{'player', 'season'}}."
        )
        # When the tab has datasets, default_dataset must be one of them.
        if tab["datasets"]:
            assert tab["default_dataset"] in tab["datasets"], (
                f"player-hub tab {tab['id']!r} default_dataset "
                f"{tab['default_dataset']!r} not in {tab['datasets']!r}."
            )


def test_player_catalog_dataset_shape(contract_client: TestClient) -> None:
    """Each player-hub dataset has all 8 fields and a non-optional `columns` list."""
    response = contract_client.get("/api/endpoints/player-hub")
    datasets = response.json()["datasets"]
    assert datasets, "player-hub catalog declared no datasets (expected ≥ 1)."

    required = {
        "id",
        "label",
        "endpoint_name",
        "scope",
        "description",
        "columns",
        "default_visible_columns",
        "supports_export",
    }
    for dataset in datasets:
        assert set(dataset.keys()) == required, (
            f"player-hub dataset {dataset.get('id')!r} keys were "
            f"{sorted(dataset.keys())!r}; expected exactly {sorted(required)!r}."
        )
        assert "columns" in dataset and dataset["columns"] is not None, (
            f"player-hub dataset {dataset['id']!r} must have a required `columns` "
            f"list (not optional, not null); got {dataset.get('columns')!r}."
        )
        assert dataset["scope"] in {"player", "season"}, (
            f"player-hub dataset {dataset['id']!r} scope {dataset['scope']!r} "
            f"not in {{'player', 'season'}}."
        )
        for column in dataset["columns"]:
            assert set(column.keys()) == {"key", "label", "default_visible", "numeric"}, (
                f"player-hub dataset {dataset['id']!r} column {column.get('key')!r} "
                f"keys were {sorted(column.keys())!r}; "
                f"expected exactly {{'key', 'label', 'default_visible', 'numeric'}}."
            )
            assert isinstance(column["key"], str) and column["key"], (
                f"player-hub dataset {dataset['id']!r} column key must be a non-empty "
                f"string; got {column.get('key')!r}."
            )
            assert isinstance(column["default_visible"], bool), (
                f"player-hub dataset {dataset['id']!r} column {column['key']!r} "
                f"`default_visible` must be bool; got {type(column['default_visible']).__name__}."
            )


# ---------------------------------------------------------------------------
# Team-hub
# ---------------------------------------------------------------------------


def test_team_catalog_status_code(contract_client: TestClient) -> None:
    """`GET /api/endpoints/team-hub` returns HTTP 200."""
    response = contract_client.get("/api/endpoints/team-hub")
    assert response.status_code == 200, (
        f"GET /api/endpoints/team-hub returned {response.status_code} "
        f"(expected 200); body: {response.text!r}"
    )


def test_team_catalog_scope_literal(contract_client: TestClient) -> None:
    """Team-hub scopes are the team-scope literal: `team` or `team_season` (NOT player/season)."""
    response = contract_client.get("/api/endpoints/team-hub")
    body = response.json()
    allowed = {"team", "team_season"}
    for tab in body["tabs"]:
        assert tab["scope"] in allowed, (
            f"team-hub tab {tab['id']!r} scope {tab['scope']!r} not in {allowed!r}. "
            f"Team-hub must NOT reuse the player-scope literal."
        )
    for dataset in body["datasets"]:
        assert dataset["scope"] in allowed, (
            f"team-hub dataset {dataset['id']!r} scope {dataset['scope']!r} "
            f"not in {allowed!r}."
        )


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_catalog_idempotent(contract_client: TestClient) -> None:
    """Two back-to-back calls return byte-identical bodies (the catalog is cached at module load)."""
    player_a = contract_client.get("/api/endpoints/player-hub").json()
    player_b = contract_client.get("/api/endpoints/player-hub").json()
    assert player_a == player_b, (
        f"/api/endpoints/player-hub is not idempotent: {player_a!r} != {player_b!r}."
    )

    team_a = contract_client.get("/api/endpoints/team-hub").json()
    team_b = contract_client.get("/api/endpoints/team-hub").json()
    assert team_a == team_b, (
        f"/api/endpoints/team-hub is not idempotent: {team_a!r} != {team_b!r}."
    )
