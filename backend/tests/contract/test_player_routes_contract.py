"""HTTP contract tests for Player Hub runtime routes."""

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
    reason="player route contracts require the real DuckDB snapshot",
)


def test_player_search_returns_bbr_slug(contract_client: TestClient) -> None:
    response = contract_client.get("/api/players/search", params={"term": "curry"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(row["identifier"] == "curryst01" for row in body)
    assert {"name", "identifier", "leagues"} <= set(body[0])


def test_player_search_short_term_uses_error_envelope(
    contract_client: TestClient,
) -> None:
    response = contract_client.get("/api/players/search", params={"term": "j"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_search"


def test_player_featured_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/players/featured")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"athletes"}
    assert {athlete["identifier"] for athlete in body["athletes"]} >= {
        "jamesle01",
        "jordami01",
        "curryst01",
        "birdla01",
    }


def test_player_summary_embeds_career_dataset(contract_client: TestClient) -> None:
    response = contract_client.get("/api/players/jamesle01/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["identifier"] == "jamesle01"
    assert body["display_name"] == "LeBron James"
    assert body["default_season"] in body["available_seasons"]
    assert body["hero_stats"]["points_per_game"] > 20
    assert body["career"]["dataset"] == "career"
    assert body["career"]["row_count"] > 0


def test_player_dataset_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/players/jamesle01/career")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "dataset",
        "endpoint_name",
        "params",
        "row_count",
        "columns",
        "default_visible_columns",
        "rows",
    }
    assert body["dataset"] == "career"
    assert {"gp", "pts", "ast", "reb"} <= set(body["rows"][0])


def test_player_shooting_dataset_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/players/curryst01/shooting")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"] == "shooting"
    assert body["row_count"] > 0
    assert {"fgm", "fga", "fg_pct", "fg3m", "fg3a", "fg3_pct"} <= set(
        body["rows"][0]
    )


def test_player_season_dataset_shape(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/api/players/jamesle01/seasons/2024/adjusted-shooting"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"] == "adjusted-shooting"
    assert body["params"]["season_end_year"] == 2024
    assert body["row_count"] > 0


def test_player_export_csv(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/api/players/jamesle01/export",
        params={"dataset": "career"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == "gp,pts,ast,reb,stl,blk,tov,mp"
