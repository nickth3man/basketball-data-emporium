from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from basketball_data_emporium.db.pool import DuckDBPool


class _Conn:
    def close(self) -> None:
        return None


class _BlockingTestPool(DuckDBPool):
    def __init__(self) -> None:
        super().__init__(Path("unused.duckdb"), size=1)
        self.open_count = 0

    def _open_one(self) -> Any:  # type: ignore[override]
        self.open_count += 1
        return _Conn()


def test_pool_blocks_when_saturated_instead_of_opening_extra_connection() -> None:
    pool = _BlockingTestPool()
    first = pool.acquire()
    acquired = threading.Event()
    finished = threading.Event()

    def worker() -> None:
        second = pool.acquire()
        acquired.set()
        pool.release(second)
        finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.05)

    assert pool.open_count == 1
    assert not acquired.is_set()

    pool.release(first)
    assert finished.wait(timeout=1)
    thread.join(timeout=1)
    assert pool.open_count == 1
