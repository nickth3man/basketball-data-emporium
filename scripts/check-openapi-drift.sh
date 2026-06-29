#!/usr/bin/env bash
# CI drift gate: regenerate openapi-types.ts in-process and compare it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"
COMMITTED_TS="${FRONTEND_DIR}/src/lib/openapi-types.ts"

TMPDIR_FOR_BASH="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}"
LIVE_SPEC="${TMPDIR_FOR_BASH}/drift-openapi.json"
LIVE_TYPES="${TMPDIR_FOR_BASH}/drift-types.ts"

cleanup() {
    rm -f "$LIVE_SPEC" "$LIVE_TYPES"
}
trap cleanup EXIT

cd "$BACKEND_DIR"
LIVE_SPEC="$LIVE_SPEC" uv run python - <<'PY'
import json
import os

from fastapi.testclient import TestClient

from basketball_data_emporium.server.app import app

spec = TestClient(app).get("/openapi.json").json()
with open(os.environ["LIVE_SPEC"], "w", encoding="utf-8") as f:
    json.dump(spec, f)
PY

cd "$FRONTEND_DIR"
node "$FRONTEND_DIR/node_modules/openapi-typescript/bin/cli.js" \
    "$LIVE_SPEC" -o "$LIVE_TYPES" < /dev/null

LIVE_TYPES="$LIVE_TYPES" COMMITTED_TS="$COMMITTED_TS" python - <<'PY'
import difflib
import os
import sys

generated = open(os.environ["LIVE_TYPES"], encoding="utf-8").read()
committed = open(os.environ["COMMITTED_TS"], encoding="utf-8").read()

if generated == committed:
    print("No drift detected.")
    sys.exit(0)

diff = "".join(
    difflib.unified_diff(
        generated.splitlines(keepends=True),
        committed.splitlines(keepends=True),
        fromfile="generated/openapi-types.ts",
        tofile="committed/openapi-types.ts",
    )
)
print("OpenAPI drift detected:", file=sys.stderr)
print(diff, file=sys.stderr)
sys.exit(1)
PY
