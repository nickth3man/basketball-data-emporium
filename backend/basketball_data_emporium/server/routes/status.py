"""`GET /api/status` — liveness + DuckDB ping.

The endpoint runs `SELECT 1` against a read-only connection from the
pool to confirm the database is reachable. On success it returns
`{"ok": true, "endpoint_count": 18}` (18 is the static total of
public endpoints, not a live count). On any failure the domain
exception is raised and `_map_exception` in `app.py` turns it into
the standard `{ detail: { code, message, detail } }` envelope.
"""

from __future__ import annotations

import duckdb
from fastapi import APIRouter, Depends, status

from basketball_data_emporium.schemas.common import StatusResponse
from basketball_data_emporium.server.deps import get_db
from basketball_data_emporium.server.errors import InternalError
from basketball_data_emporium.server.status_audit import read_audit_status

router = APIRouter(tags=["status"])


# Static total of public endpoints. Informational only; the route
# modules are the source of truth for the live endpoint list.
ENDPOINT_COUNT: int = 18


@router.get(
    "/api/status",
    response_model=StatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Status",
    operation_id="status_api_status_get",
)
def get_status(
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
) -> StatusResponse:
    try:
        row = conn.execute("SELECT 1").fetchone()
    except Exception as exc:  # noqa: BLE001 — convert any DB error to API error
        raise InternalError(
            "DuckDB ping failed",
            detail={"reason": str(exc)},
        ) from exc

    if row is None or row[0] != 1:
        # Defensive: the pool's startup probe should have caught this,
        # but if a connection ever returns a malformed result we surface
        # it as `internal_error` rather than `ok: true`.
        raise InternalError(
            "DuckDB ping returned an unexpected result",
            detail={"result": list(row) if row is not None else None},
        )

    audit = read_audit_status(conn)

    return StatusResponse(
        ok=True,
        endpoint_count=ENDPOINT_COUNT,
        data_state=audit.state,
        data_state_reason=audit.reason,
        data_verified=audit.is_verified,
        data_stale=audit.is_stale,
        latest_pipeline_run_id=audit.latest_run_id,
        latest_pipeline_stage=audit.latest_stage,
        latest_pipeline_status=audit.latest_status,
        latest_pipeline_started_at=audit.latest_started_at,
        latest_dq_status=audit.dq_status,
    )
