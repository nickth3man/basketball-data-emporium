"""7-day rolling log retention sweep.

On startup, this module walks every known log root under
``settings.chat_log_dir`` and unlinks files whose mtime is older than
``retention_days`` (default 7). It is safe to call repeatedly: each
invocation is a fresh directory walk and the deletion pass is best-effort
— a single permission error is logged and skipped so one unreadable file
cannot prevent the sweep from completing.

Sweep contract
--------------
* Only files (not directories) are removed. Empty leaf directories created
  by past sweeps are then ``rmdir``'d if they fall out of the retention
  window, so the log root stays tidy.
* ``sweep_all`` never raises — failures are caught, logged, and counted
  as zero. The lifespan startup must keep going even if the disk is
  read-only or a file is locked.
* ``now`` is overridable for tests (no need to monkeypatch ``time.time``).

Log roots covered:

* ``{chat_log_dir}/app/<date>.jsonl``         (root JSONL app log)
* ``{chat_log_dir}/queries/<date>/...``       (per-turn SQL + results)
* ``{chat_log_dir}/model/<date>/...``         (per-turn token usage)

Visible session history under ``chat_data_dir/sessions/`` is NOT swept
here — the user manually clears it via ``DELETE /api/sessions/{id}``.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import logging
import os
import time
from pathlib import Path
from typing import Any

from .config import get_settings

log = logging.getLogger(__name__)

RETENTION_DAYS: int = 7

LOG_SUBDIRS: tuple[str, ...] = ("app", "queries", "model")


def _cutoff_epoch(retention_days: int, *, now: float | None = None) -> float:
    """Return the epoch cutoff: files older than this are candidates.

    ``retention_days=0`` ⇒ keep nothing older than now (i.e. delete
    everything that exists — useful for an aggressive test sweep).
    """
    reference = now if now is not None else time.time()
    return reference - retention_days * 86400


def _is_under_retention(path: Path, cutoff: float) -> bool:
    """True when ``path`` should be deleted (its mtime is older than cutoff)."""
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _delete_expired_files(root: Path, cutoff: float) -> int:
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]
        for name in filenames:
            path = directory / name
            if path.is_symlink() or not _is_under_retention(path, cutoff):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                log.warning("sweep_logs: failed to delete %s: %s", path, exc)
    return removed


def _remove_empty_directories(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        if Path(dirpath) == root or dirnames or filenames:
            continue
        with contextlib.suppress(OSError):
            os.rmdir(dirpath)


def sweep_logs(
    root: Path,
    retention_days: int = RETENTION_DAYS,
    *,
    now: float | None = None,
) -> int:
    """Delete files under ``root`` older than ``retention_days``.

    Returns the number of files removed. Only files (not directories) are
    unlinked; empty leaf directories left behind are then ``rmdir``'d.
    IO errors are swallowed (logged at WARNING) so one bad file cannot
    abort the whole sweep.
    """
    if retention_days < 0:
        log.warning("sweep_logs: negative retention_days=%d; nothing to do", retention_days)
        return 0

    if not root.exists():
        return 0
    if not root.is_dir():
        log.warning("sweep_logs: %s exists but is not a directory; skipping", root)
        return 0

    cutoff = _cutoff_epoch(retention_days, now=now)
    removed = _delete_expired_files(root, cutoff)
    _remove_empty_directories(root)
    return removed


def sweep_all(retention_days: int = RETENTION_DAYS) -> dict[str, int]:
    """Sweep every known log root under ``settings.chat_log_dir``.

    Returns a mapping ``{subdir_name: files_removed}``. Never raises —
    errors are caught, logged, and reported as ``0`` so the caller can
    safely log the result without ``try/except`` of its own.
    """
    settings: Any = get_settings()
    log_root = Path(settings.chat_log_dir)
    results: dict[str, int] = {}
    for subdir in LOG_SUBDIRS:
        root = log_root / subdir
        try:
            results[subdir] = sweep_logs(root, retention_days)
        except Exception:  # noqa: BLE001
            log.exception("sweep_all: unhandled error sweeping %s", root)
            results[subdir] = 0
    return results


__all__ = [
    "LOG_SUBDIRS",
    "RETENTION_DAYS",
    "sweep_all",
    "sweep_logs",
]


def _today_stamp() -> str:
    """YYYY-MM-DD UTC; mirrors ``logging_setup`` and ``pipeline`` helpers.

    Kept here as a module-private helper so the file remains
    self-contained when imported by other tools (e.g. a future CLI that
    runs the sweep without a lifespan).
    """
    return _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d")
