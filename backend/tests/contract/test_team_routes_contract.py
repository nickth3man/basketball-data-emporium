"""HTTP contract tests for Team Hub runtime routes."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_team_search_returns_bbr_abbrev(contract_client: TestClient) -> None:
    response = contract_client.get("/api/teams/search", params={"term": "bos"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["identifier"] == "BOS"
    assert body[0]["name"] == "Boston Celtics"


def test_team_search_short_term_uses_error_envelope(
    contract_client: TestClient,
) -> None:
    response = contract_client.get("/api/teams/search", params={"term": "b"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_search"


def test_team_featured_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/teams/featured")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"teams"}
    assert {team["identifier"] for team in body["teams"]} >= {
        "LAL",
        "BOS",
        "GSW",
        "CHI",
    }


def test_team_summary_embeds_roster_and_arc(contract_client: TestClient) -> None:
    response = contract_client.get("/api/teams/BOS/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["identifier"] == "BOS"
    assert body["display_name"] == "Boston Celtics"
    assert body["default_season"] in body["available_seasons"]
    assert body["hero_stats"]["wins"] is not None
    assert body["roster"]["dataset"] == "roster"
    assert body["roster"]["row_count"] > 0
    assert len(body["franchise_arc"]) > 0


def test_team_dataset_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/teams/BOS/roster")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"] == "roster"
    assert {"full_name", "gp", "mp", "per"} <= set(body["rows"][0])


def test_team_season_dataset_shape(contract_client: TestClient) -> None:
    response = contract_client.get("/api/teams/BOS/seasons/2024/roster")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"] == "roster"
    assert body["params"]["season_end_year"] == 2024
    assert body["row_count"] > 0


def test_team_export_csv(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/api/teams/BOS/export",
        params={"dataset": "roster", "season_end_year": "2024"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == "full_name,gp,mp,per"
