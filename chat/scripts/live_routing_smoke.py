"""Live routing smoke across all 20 benchmark questions (Phase 8 model-selection test).

For each registered template, takes its first EXAMPLE prompt, runs the live
OpenRouter agent, and checks whether the routed template_id matches the
template that provided the example (a not-answerable/clarification result is
also acceptable for the NA template). Prints a PASS/FAIL table + accuracy %.

Run:
    uv run python scripts/live_routing_smoke.py

Makes ~20 live OpenRouter calls (~$0.02 on mistral-small). Does NOT need the
HTTP server — it calls the agent directly.
"""

from __future__ import annotations

import asyncio
import sys
import time

from chat_server.agent import get_agent, make_deps
from chat_server.templates import get_registry

# Templates whose example question legitimately routes to a non-template path.
EXPECT_NON_TEMPLATE = {"season_comparison.player_team_split"}


async def _run_one(agent, deps, template_id: str, prompt: str) -> tuple[str, str]:
    try:
        result = await asyncio.wait_for(agent.run(prompt, deps=deps), timeout=45.0)
        plan = result.output
        if plan.clarification:
            return ("CLARIFY", "")
        if plan.not_answerable_note:
            return ("NA", "")
        return (plan.template_id or "<empty>", "")
    except TimeoutError:
        return ("TIMEOUT", "")
    except Exception as exc:  # noqa: BLE001 - report any failure mode
        return ("ERROR", f"{type(exc).__name__}: {exc!s:.80}")


async def main() -> int:
    registry = get_registry()
    agent = get_agent()
    deps = await make_deps()

    rows: list[tuple[str, str, str, str]] = []
    pass_count = 0
    for tid in sorted(registry):
        tmpl = registry[tid]
        prompt = tmpl.examples[0] if tmpl.examples else "(no example)"
        t0 = time.perf_counter()
        routed, detail = await _run_one(agent, deps, tid, prompt)
        dt = time.perf_counter() - t0
        ok = routed in ("NA", "CLARIFY", tid) if tid in EXPECT_NON_TEMPLATE else routed == tid
        pass_count += ok
        rows.append((tid, routed, f"{dt:.1f}s", "PASS" if ok else "FAIL"))
        print(f"[{'PASS' if ok else 'FAIL'}] {dt:4.1f}s  {tid:48s} -> {routed}{detail}")

    total = len(rows)
    print(f"\nRouting accuracy: {pass_count}/{total} ({100 * pass_count / total:.0f}%)")
    failures = [r for r in rows if r[3] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
