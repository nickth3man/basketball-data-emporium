"""Dev CLI: run a registered template with hard-coded params and print the QueryResult.

Phase 1 exit criterion (PLAN §15): a developer-facing CLI that runs a
template directly against the warehouse, with no agent in the loop.

Usage:
    uv run python scripts/run_template.py season_thresholds.fifty_forty_ninety
    uv run python scripts/run_template.py                          # default template
    uv run python scripts/run_template.py season_thresholds.fifty_forty_ninety --min_ppg 30
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from chat_server.db import get_db
from chat_server.templates import get_template, list_templates

DEFAULT_TEMPLATE_ID = "season_thresholds.fifty_forty_ninety"


async def run_template(template_id: str, overrides: dict[str, Any]) -> None:
    """Resolve the template, build params, execute, print the QueryResult."""
    tmpl = get_template(template_id)
    params = tmpl.params_model(**overrides)
    db = get_db()

    print(f"Template: {tmpl.template_id}")
    print(f"Title:    {tmpl.title}")
    print(f"Params:   {params.model_dump()}")
    print(f"SQL:\n{tmpl.sql}")

    result = await db.execute(tmpl.sql, params.model_dump(), limit=tmpl.default_limit)

    print(
        f"\nRows: {result.row_count} (truncated={result.truncated}) in {result.duration_ms:.1f}ms"
    )
    print(f"Columns: {result.columns}")
    for row in result.rows[:10]:
        print(row)


def _parse_overrides(argv: list[str]) -> dict[str, Any]:
    """Parse ``--key value`` pairs after the template id."""
    overrides: dict[str, Any] = {}
    it = iter(argv)
    for token in it:
        if not token.startswith("--"):
            raise SystemExit(f"unexpected positional argument: {token!r}")
        key = token[2:]
        try:
            value = next(it)
        except StopIteration as exc:
            raise SystemExit(f"flag --{key} needs a value") from exc
        # Cast numeric-looking values; everything else stays a string.
        try:
            overrides[key] = int(value)
        except ValueError:
            try:
                overrides[key] = float(value)
            except ValueError:
                overrides[key] = value
    return overrides


def main(argv: list[str] | None = None) -> None:
    """Parse CLI args and dispatch to ``run_template``."""
    parser = argparse.ArgumentParser(
        description="Run a registered template with hard-coded params.",
    )
    parser.add_argument(
        "template_id",
        nargs="?",
        default=DEFAULT_TEMPLATE_ID,
        help=(
            f"Template id to run (default: {DEFAULT_TEMPLATE_ID}). "
            f"Use `list` to see all registered templates."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List registered templates and exit.",
    )
    args, remaining = parser.parse_known_args(argv)

    if args.list or args.template_id == "list":
        print("Registered templates:")
        for tmpl in list_templates():
            print(f"  {tmpl.template_id}  [{tmpl.capability}]  {tmpl.title}")
        return

    overrides = _parse_overrides(remaining)
    # Hard-coded default params when the user passes no overrides — the
    # Phase 1 minimal CLI runs the canonical 50-40-90 + 25 PPG query.
    if not overrides:
        overrides = {"min_ppg": 25.0}

    asyncio.run(run_template(args.template_id, overrides))


if __name__ == "__main__":
    main(sys.argv[1:])
