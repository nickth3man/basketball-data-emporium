"""HTTP contract tests for Season Hub runtime routes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _has_duckdb_file() -> bool:
    raw = os.environ.get("DUCKDB_PATH", "../data/nba.duckdb")
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path.exists()


pytestmark = pytest.mark.skipif(
    not _has_duckdb_file(),
    reason="season route contracts require the real DuckDB snapshot",
)


def test_available_seasons_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/seasons")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"seasons", "default_season"}
    assert body["seasons"]
    assert body["default_season"] == body["seasons"][0]
    assert 2024 in body["seasons"]


def test_season_standings_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/seasons/2024/standings")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"] == "standings"
    assert body["endpoint_name"] == "season_standings"
    assert body["params"]["season_end_year"] == 2024
    assert body["row_count"] >= 30
    assert {"team", "wins", "losses", "win_pct", "net_rating"} <= set(
        body["rows"][0]
    )
    assert any(row["team"] == "BOS" and row["wins"] == 64 for row in body["rows"])


def test_season_leaders_shape_and_stat_order(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/api/seasons/2024/leaders",
        params={"stat": "pts"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"] == "leaders"
    assert body["endpoint_name"] == "season_leaders"
    assert body["params"] == {
        "season_end_year": 2024,
        "season_type": "Regular",
        "stat": "pts",
    }
    assert body["row_count"] > 0
    first = body["rows"][0]
    assert first["full_name"] == "Luka Doncic"
    assert first["pts_rank"] == 1
    assert {"avg_pts", "avg_reb", "avg_ast", "gp"} <= set(first)


def test_season_leaders_reject_unknown_stat(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/api/seasons/2024/leaders",
        params={"stat": "minutes"},
    )
    assert response.status_code == 422


def test_season_standings_invalid_season_uses_error_envelope(
    contract_client: TestClient,
) -> None:
    response = contract_client.get("/api/seasons/1800/standings")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "invalid_season"
