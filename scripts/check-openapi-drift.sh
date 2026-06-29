#!/usr/bin/env bash
# CI drift gate: regenerate openapi-types.ts from the live sidecar and compare.
#
# Usage: ./scripts/check-openapi-drift.sh
# Exits 0 if no drift, 1 if drift detected.
#
# This script is the shell-wrapper equivalent of
# `backend/tests/openapi/test_drift.py::test_openapi_drift_gate`. It uses
# FastAPI's TestClient (in-process, no server) to fetch /openapi.json,
# regenerates the TypeScript types with openapi-typescript, and diffs the
# Phase-1 sections against the committed file.
#
# Phase-1 scope: the `StatusResponse` component, the `/api/status` path
# entry, and the `status_api_status_get` operation's `200` response. The
# full-file structural diff lands when all 15 endpoints exist in Phase 2+.
#
# TODO P3-OPS-03: now that all 15 planned paths exist, promote this from the
# historical Phase-1 subset to a full structural drift gate. Keep the
# in-process spec generation, but compare every path/schema/operation that the
# frontend imports, or compare the whole generated file if generator output is
# stable enough.

set -euo pipefail

# Resolve repo root (parent of this script's directory) regardless of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"
COMMITTED_TS="${FRONTEND_DIR}/src/lib/openapi-types.ts"

# Temp files for the round-trip. Use the system temp dir on all platforms
# (Git Bash on Windows maps $TMPDIR, but fall back to a sane default).
TMPDIR_FOR_BASH="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}"
TMPDIR_FOR_PY="$TMPDIR_FOR_BASH"
LIVE_SPEC="${TMPDIR_FOR_BASH}/drift-openapi.json"
LIVE_TYPES="${TMPDIR_FOR_BASH}/drift-types.ts"

# Cleanup on exit (success or failure).
cleanup() {
    rm -f "$LIVE_SPEC" "$LIVE_TYPES"
}
trap cleanup EXIT

# --- Step 1: fetch the live spec via TestClient (no server, no port) ---
cd "$BACKEND_DIR"
uv run python - <<PY
import json
import os
import sys

from fastapi.testclient import TestClient

from courtside_data.server.app import app

spec = TestClient(app).get("/openapi.json").json()
out_path = os.environ["LIVE_SPEC"]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(spec, f)
PY

# --- Step 2: regenerate types via openapi-typescript ---
cd "$FRONTEND_DIR"
LIVE_SPEC="$LIVE_SPEC" node "$FRONTEND_DIR/node_modules/openapi-typescript/bin/cli.js" \
    "$LIVE_SPEC" -o "$LIVE_TYPES" < /dev/null

# --- Step 3: diff the Phase-1 sections via a small Python helper ---
LIVE_SPEC="$LIVE_SPEC" \
LIVE_TYPES="$LIVE_TYPES" \
COMMITTED_TS="$COMMITTED_TS" \
uv run --project "$BACKEND_DIR" python - <<'PY'
import difflib
import os
import re
import sys


def extract_block(text: str, key: str) -> str | None:
    """Return the `key: { ... };` block (or `}`-terminated for paths)."""
    pattern = re.compile(
        rf"^\s{{0,12}}{re.escape(key)}\s*:\s*\{{",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return None
    start = match.start()
    depth = 0
    i = match.end() - 1
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                if end < len(text) and text[end] == ";":
                    end += 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return text[start:end]
        i += 1
    return None


def extract_200(op_block: str) -> str | None:
    pattern = re.compile(r"^\s{0,12}200\s*:\s*\{", re.MULTILINE)
    match = pattern.search(op_block)
    if match is None:
        return None
    start = match.start()
    depth = 0
    i = match.end() - 1
    while i < len(op_block):
        ch = op_block[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                if end < len(op_block) and op_block[end] == ";":
                    end += 1
                if end < len(op_block) and op_block[end] == "\n":
                    end += 1
                return op_block[start:end]
        i += 1
    return None


def main() -> int:
    committed = open(os.environ["COMMITTED_TS"], encoding="utf-8").read()
    generated = open(os.environ["LIVE_TYPES"], encoding="utf-8").read()

    failures: list[str] = []

    # Top-level sections: full-block match.
    for label, key in [
        ("/api/status path entry", '"/api/status"'),
        ("StatusResponse schema", "StatusResponse"),
    ]:
        gen = extract_block(generated, key)
        com = extract_block(committed, key)
        if gen is None:
            failures.append(f"[{label}] missing from generated types")
            continue
        if com is None:
            failures.append(f"[{label}] missing from committed types")
            continue
        if gen != com:
            diff = "".join(
                difflib.unified_diff(
                    gen.splitlines(keepends=True),
                    com.splitlines(keepends=True),
                    fromfile=f"generated/{label}",
                    tofile=f"committed/{label}",
                )
            )
            failures.append(f"[{label}] drift detected:\n{diff}")

    # Operation: compare the 200 response only (Phase 1 scope).
    gen_op = extract_block(generated, "status_api_status_get")
    com_op = extract_block(committed, "status_api_status_get")
    if gen_op is None or com_op is None:
        failures.append("[status_api_status_get operation] block missing")
    else:
        gen_200 = extract_200(gen_op)
        com_200 = extract_200(com_op)
        if gen_200 is None or com_200 is None:
            failures.append("[status_api_status_get 200] response missing")
        elif gen_200 != com_200:
            diff = "".join(
                difflib.unified_diff(
                    gen_200.splitlines(keepends=True),
                    com_200.splitlines(keepends=True),
                    fromfile="generated/200",
                    tofile="committed/200",
                )
            )
            failures.append(
                f"[status_api_status_get 200] drift detected:\n{diff}"
            )

    if failures:
        print("OpenAPI drift detected:", file=sys.stderr)
        for f in failures:
            print(f, file=sys.stderr)
        return 1

    print("No drift detected.")
    return 0


sys.exit(main())
PY
