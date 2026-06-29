"""MVCS Test 2 — `/api/status` HTTP contract.

Distinct from `tests/test_status.py` (which exercises the envelope
shape with a stub pool). This test exercises the **full HTTP
contract** with FastAPI's in-process `TestClient`:

* status code is exactly 200
* `Content-Type: application/json`
* body parses as JSON
* body includes the pinned liveness and audit-state fields
* no `Set-Cookie`, `Cache-Control`, or other headers leak through
  (the contract is intentionally minimal)
* a second call returns the same body (the route is stateless)

The fixture in `conftest.py` uses the real pool when the DuckDB
file is on disk, or a stub when it isn't — see that file for the
resolution rule.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_status_status_code(contract_client: TestClient) -> None:
    """`/api/status` returns HTTP 200."""
    response = contract_client.get("/api/status")
    assert response.status_code == 200, (
        f"GET /api/status returned {response.status_code} (expected 200); "
        f"body: {response.text!r}"
    )


def test_status_content_type_is_json(contract_client: TestClient) -> None:
    """`/api/status` advertises `application/json`."""
    response = contract_client.get("/api/status")
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/json"), (
        f"GET /api/status Content-Type was {content_type!r}; expected application/json."
    )


def test_status_body_contract(contract_client: TestClient) -> None:
    """`/api/status` carries liveness plus audit freshness state."""
    response = contract_client.get("/api/status")
    body = response.json()
    assert body["ok"] is True
    assert body["endpoint_count"] == 15
    assert body["data_state"] in {"passed", "failed", "stale", "unverified"}
    assert body["data_state_reason"] in {
        "audit_missing",
        "latest_pipeline_failed",
        "latest_dq_failed",
        "audit_stale",
        "dq_missing",
        "verified",
        "unverified",
    }
    assert isinstance(body["data_verified"], bool)
    assert isinstance(body["data_stale"], bool)


def test_status_field_types(contract_client: TestClient) -> None:
    """`ok` is bool, `endpoint_count` is int.

    `endpoint_count` is the static constant 15; if a future phase
    changes it to a `count(*)` over a real route table, this test
    will catch a `bool` vs `int` regression.
    """
    response = contract_client.get("/api/status")
    body = response.json()
    assert isinstance(body["ok"], bool), (
        f"`ok` was {type(body['ok']).__name__}, expected bool."
    )
    assert isinstance(body["endpoint_count"], int), (
        f"`endpoint_count` was {type(body['endpoint_count']).__name__}, expected int."
    )
    assert isinstance(body["data_state"], str), (
        f"`data_state` was {type(body['data_state']).__name__}, expected str."
    )
    assert isinstance(body["data_state_reason"], str), (
        "`data_state_reason` was "
        f"{type(body['data_state_reason']).__name__}, expected str."
    )
    assert body["endpoint_count"] == 15, (
        f"`endpoint_count` was {body['endpoint_count']}; the MVCS contract pins it to 15."
    )


def test_status_no_unexpected_headers(contract_client: TestClient) -> None:
    """`/api/status` does not leak `Set-Cookie` or other stateful headers.

    The status endpoint is meant to be cacheable + side-effect-free;
    a `Set-Cookie` would silently introduce a session, which the
    frontend's `useStatus` hook does not expect.
    """
    response = contract_client.get("/api/status")
    leaky = {"set-cookie", "cache-control", "etag", "last-modified"}
    found = leaky & {k.lower() for k in response.headers.keys()}
    assert not found, f"/api/status leaked stateful headers: {sorted(found)}"


def test_status_is_idempotent(contract_client: TestClient) -> None:
    """Two back-to-back calls return byte-identical bodies.

    The route is `GET` and stateless, so a regression that made it
    return time-dependent data (e.g. `uptime_seconds`) would be
    caught here.
    """
    a = contract_client.get("/api/status").json()
    b = contract_client.get("/api/status").json()
    assert a == b, f"/api/status is not idempotent: {a!r} != {b!r}."
