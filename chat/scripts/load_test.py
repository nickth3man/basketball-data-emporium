"""Concurrent-turn load smoke (Phase 8, PLAN §15 "load test").

Fires N parallel POST /api/chat/stream turns against the LOCAL running API and
reports per-turn status + p50/p95 latency + error count.

Prerequisite: the API must already be running. Start it in a separate terminal:
    uv run uvicorn chat_server.main:app --port 8787

Then:
    uv run python scripts/load_test.py            # default: 5 concurrent turns
    uv run python scripts/load_test.py --n 10     # 10 turns

This script does NOT start the server (avoids holding the terminal). It exits
with a clear message if the server isn't reachable.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import httpx

API = "http://127.0.0.1:8787"
PROMPT = "Who shot 50/40/90 with at least 25 points per game?"


async def one_turn(client: httpx.AsyncClient, turn_id: int) -> tuple[int, str, float]:
    """Returns (turn_id, status, latency_s). status ∈ {ok, error, no_answer_finished}."""
    t0 = time.perf_counter()
    status = "error"
    try:
        async with client.stream(
            "POST",
            f"{API}/api/chat/stream",
            json={"message": PROMPT},
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                return (turn_id, "error", time.perf_counter() - t0)
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    if "answer_finished" in line:
                        status = "ok"
                    elif line.startswith("event: error"):
                        status = "error"
    except Exception:
        status = "error"
    return (turn_id, status, time.perf_counter() - t0)


async def main(n: int) -> int:
    # Verify the server is up first (don't hang on a missing server).
    async with httpx.AsyncClient() as probe:
        try:
            h = await probe.get(f"{API}/api/health", timeout=3.0)
            if h.status_code != 200:
                print(f"Server health check failed: HTTP {h.status_code}", file=sys.stderr)
                print("Start it: uv run uvicorn chat_server.main:app --port 8787", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"Server not reachable at {API}: {exc}", file=sys.stderr)
            print("Start it: uv run uvicorn chat_server.main:app --port 8787", file=sys.stderr)
            return 1

    print(f"Firing {n} concurrent turns against {API} ...")
    async with httpx.AsyncClient() as client:
        t0 = time.perf_counter()
        results = await asyncio.gather(*[one_turn(client, i) for i in range(n)])
    total = time.perf_counter() - t0

    lats = sorted(r[2] for r in results)
    ok = sum(1 for r in results if r[1] == "ok")
    err = n - ok
    p50 = statistics.median(lats)
    p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))]
    print(f"\nResults: {ok}/{n} ok, {err} errors")
    print(f"Wall time: {total:.1f}s   p50: {p50:.1f}s   p95: {p95:.1f}s")
    for tid, status, lat in results:
        print(f"  turn {tid}: {status:24s} {lat:.1f}s")
    return 0 if ok == n else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="number of concurrent turns")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.n)))
