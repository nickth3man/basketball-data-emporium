"""MVCS Test 5 — OpenAPI drift gate.

Catches any change in the FastAPI contract — operationIds, descriptions,
nullability, required-ness, key-set changes — by regenerating the TypeScript
types from the live /openapi.json and comparing against the committed file.

This test uses TestClient (in-process HTTP, no server). It writes the live
OpenAPI spec to a temp file, runs openapi-typescript, and compares the
StatusResponse + /api/status sections against the committed file.

For Phase 1, only the /api/status endpoint exists. The comparison is scoped
to the sections that should match. When Phase 2+ adds endpoints, expand the
comparison scope (or move to a full-file structural diff once all 15 exist).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from basketball_data_emporium.server.app import app


# ---------------------------------------------------------------------------
# openapi-typescript invocation
#
# We invoke the CLI via `node` + the package's own `bin/cli.js` rather than
# `npx`. Reasons:
#   1. On Windows, `npx` is `npx.cmd` — Python's `subprocess.run` with a
#      list of args won't auto-resolve `.cmd` extensions (only `shell=True`
#      does, which we'd rather avoid for safety).
#   2. The CLI is already pinned in `frontend/package.json`, so the version
#      we run matches the one that generated the committed file.
# ---------------------------------------------------------------------------


def _openapi_typescript_cli(frontend_dir: str) -> str | None:
    """Absolute path to the openapi-typescript CLI entry point."""
    path = Path(frontend_dir) / "node_modules" / "openapi-typescript" / "bin" / "cli.js"
    return str(path) if path.exists() else None


# ---------------------------------------------------------------------------
# Extraction helpers
#
# openapi-typescript emits deterministic, indentation-stable output. We find
# the opening line of a named key (e.g. `StatusResponse: {`) and walk the
# braces to find the matching closer at column 0 of the next key. This is
# intentionally simple — the codegen is the source of truth, not a TS parser.
# ---------------------------------------------------------------------------


def _extract_block(text: str, key: str, terminator: str = "};") -> str | None:
    """Return the full `key: { ... };` block from `text`, or None.

    `key` is the property name only (e.g. `"/api/status"`, `StatusResponse`,
    `status_api_status_get`). The block extends from the line that starts
    with `key:` to the matching closing brace at depth 0, including the
    optional trailing `;` and newline.

    The `terminator` parameter is currently unused (the brace-walk handles
    both `}` and `};` closing forms). It's kept for documentation and for
    potential future use (e.g. extracting non-brace-terminated regions).
    """
    del terminator  # currently unused; see docstring

    # Find the line that starts with the key followed by `: {`.
    pattern = re.compile(
        rf"^\s{{0,12}}{re.escape(key)}\s*:\s*\{{",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return None

    start = match.start()
    depth = 0
    i = match.end() - 1  # index of the opening `{`
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # Include the closing brace and optional trailing `;` and
                # the newline that follows.
                end = i + 1
                if end < len(text) and text[end] == ";":
                    end += 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return text[start:end]
        i += 1
    return None


def _read(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _extract_200_response(op_block: str) -> str | None:
    """Return the `200: { ... };` response block from an operation block.

    Scoped to the success response for Phase 1. The committed file
    includes additional error responses (400/404/429/500) that the
    current Phase-1 spec doesn't declare; those land with Phase 2.
    """
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


# ---------------------------------------------------------------------------
# The drift test
# ---------------------------------------------------------------------------


def test_openapi_drift_gate(
    committed_types_path: str,
    frontend_dir: str,
) -> None:
    """Regenerate types from the live spec and diff the Phase-1 sections.

    Asserts that the committed `openapi-types.ts` matches what
    `openapi-typescript` would produce right now from the FastAPI app.

    Phase-1 scope: the `StatusResponse` component, the `/api/status` path
    entry, and the `status_api_status_get` operation. When Phase 2+ lands,
    expand this to a full-file structural diff.
    """
    # 1. Fetch the live spec via TestClient (no server, no port binding).
    client = TestClient(app)
    spec = client.get("/openapi.json").json()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as spec_file:
        json.dump(spec, spec_file)
        spec_path = spec_file.name

    types_path = spec_path.removesuffix(".json") + ".ts"

    try:
        # 2. Regenerate types. `stdin=DEVNULL` so node can't hang waiting
        # for input; 30s timeout so a broken toolchain fails fast.
        cli_js = _openapi_typescript_cli(frontend_dir)
        if cli_js is None and os.name == "nt":
            cmd = ["cmd.exe", "/d", "/s", "/c", f"npx --yes openapi-typescript {spec_path} -o {types_path}"]
        elif cli_js is None:
            cmd = ["npx", "--yes", "openapi-typescript", spec_path, "-o", types_path]
        else:
            cmd = ["node", cli_js, spec_path, "-o", types_path]
        proc = subprocess.run(
            cmd,
            cwd=frontend_dir,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"openapi-typescript failed (rc={proc.returncode}):\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
    finally:
        Path(spec_path).unlink(missing_ok=True)

    try:
        generated = _read(types_path)
        committed = _read(committed_types_path)

        # 3a. Top-level sections: every path entry and every shared
        # schema that the live app exposes. These should match exactly
        # (the committed file must be a faithful regen of the live spec).
        top_sections = [
            # /api/status — the Phase 1 liveness endpoint.
            ("/api/status path entry", '"/api/status"'),
            ("StatusResponse schema", "StatusResponse"),
            # Phase 2 catalog endpoints.
            ("/api/endpoints/player-hub path entry", '"/api/endpoints/player-hub"'),
            ("PlayerHubCatalog schema", "PlayerHubCatalog"),
            ("/api/endpoints/team-hub path entry", '"/api/endpoints/team-hub"'),
            ("TeamHubCatalog schema", "TeamHubCatalog"),
        ]
        for label, key in top_sections:
            gen_block = _extract_block(generated, key)
            com_block = _extract_block(committed, key)
            assert gen_block is not None, (
                f"Generated types are missing the {label!r} block "
                f"(key={key!r}). The codegen output may have changed shape."
            )
            assert com_block is not None, (
                f"Committed types are missing the {label!r} block "
                f"(key={key!r}). The test scope may need updating."
            )
            assert gen_block == com_block, (
                f"Drift detected in {label}.\n"
                f"--- generated ---\n{gen_block}\n"
                f"--- committed  ---\n{com_block}\n"
                f"--- diff ---\n"
                f"{_unified_diff(gen_block, com_block, label)}"
            )

        # 3b. The Phase 1 `status_api_status_get` operation. We compare
        # only the 200 response because the committed file declares 5
        # responses (200, 400, 404, 429, 500) — the full error envelope
        # catalog — while the current Phase-1 route only declares the
        # success response (200) via `response_model=StatusResponse`.
        # The error responses will be wired up when the error-handling
        # routes land in a later phase.
        gen_op = _extract_block(generated, "status_api_status_get")
        com_op = _extract_block(committed, "status_api_status_get")
        assert gen_op is not None and com_op is not None, (
            "status_api_status_get operation block missing from one or both files"
        )
        gen_200 = _extract_200_response(gen_op)
        com_200 = _extract_200_response(com_op)
        assert gen_200 is not None and com_200 is not None, (
            "200 response missing from status_api_status_get in one or both files"
        )
        assert gen_200 == com_200, (
            "Drift detected in status_api_status_get 200 response.\n"
            f"--- generated ---\n{gen_200}\n"
            f"--- committed  ---\n{com_200}\n"
            f"--- diff ---\n"
            f"{_unified_diff(gen_200, com_200, 'status_api_status_get 200')}"
        )

        # 3c. The Phase 2 catalog operations. Same 200-only scope as
        # 3b: the catalog routes don't declare error responses, so we
        # only need to compare the 200 success shape.
        for op_id in ("catalog_api_endpoints_player_hub_get", "team_catalog_api_endpoints_team_hub_get"):
            gen_op = _extract_block(generated, op_id)
            com_op = _extract_block(committed, op_id)
            assert gen_op is not None and com_op is not None, (
                f"{op_id} operation block missing from one or both files"
            )
            gen_200 = _extract_200_response(gen_op)
            com_200 = _extract_200_response(com_op)
            assert gen_200 is not None and com_200 is not None, (
                f"200 response missing from {op_id} in one or both files"
            )
            assert gen_200 == com_200, (
                f"Drift detected in {op_id} 200 response.\n"
                f"--- generated ---\n{gen_200}\n"
                f"--- committed  ---\n{com_200}\n"
                f"--- diff ---\n"
                f"{_unified_diff(gen_200, com_200, f'{op_id} 200')}"
            )
    finally:
        Path(types_path).unlink(missing_ok=True)


def _unified_diff(a: str, b: str, label: str) -> str:
    """Tiny diff formatter for assertion messages (avoids importing difflib at top)."""
    import difflib

    return "\n".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=f"generated/{label}",
            tofile=f"committed/{label}",
        )
    )
