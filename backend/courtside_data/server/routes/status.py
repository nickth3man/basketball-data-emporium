"""`GET /api/status` — liveness + DuckDB ping.

The endpoint runs `SELECT 1` against a read-only connection from the
pool to confirm the database is reachable. On success it returns
`{"ok": true, "endpoint_count": 15}` (15 is the static total of
planned endpoints, not a live count). On any failure the domain
exception is raised and `_map_exception` in `app.py` turns it into
the standard `{ detail: { code, message, detail } }` envelope.
"""

from __future__ import annotations

import duckdb
from fastapi import APIRouter, Depends, status

from courtside_data.schemas.common import StatusResponse
from courtside_data.server.deps import get_db
from courtside_data.server.errors import InternalError

router = APIRouter(tags=["status"])


# TODO P1-BE-01: extend `StatusResponse` with audit/DQ freshness fields. The
# status endpoint should query `audit.pipeline_run_log` and `audit.dq_results`
# through `server/status_audit.py`, then surface states such as verified,
# failed, stale, and unverified. Today `ok=true` only means "DuckDB answered
# SELECT 1"; it does not mean the latest ETL passed.

# Static total of planned endpoints. Informational only; the catalog
# endpoint (Phase 2) is the source of truth for the live endpoint list.
ENDPOINT_COUNT: int = 15


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

    return StatusResponse(ok=True, endpoint_count=ENDPOINT_COUNT)
