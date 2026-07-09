"""JSONL + secret-redaction logging setup.

Phase 2 (this file): configures the root logger with a redacting filter
plus a basic JSONL handler that writes to ``{chat_log_dir}/app/<date>.jsonl``.

Phase 4/7 will add per-session/query/model JSONL handlers and a 7-day
rolling retention sweep.

Design
------
* One `logging.Filter` (`RedactingFilter`) that:
    1. Strips OpenRouter API keys (``sk-or-...``) from ``record.msg`` and
       ``record.args`` (both tuple- and dict-form).
    2. Delegates to ``loggingredactor.CommonPIIRedactingFilter`` (when
       installed) so phone numbers, emails, etc., are redacted too.
* One `logging.Formatter` (`JsonlFormatter`) emitting one JSON object per
  line: ``ts``, ``level``, ``logger``, ``message``, plus ``exc_info`` when
  the record carries an exception.
* `setup_logging()` is idempotent (guarded by a module-level flag) so the
  FastAPI lifespan, the dev CLI, and the test suite can all call it
  without duplicating handlers.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import logging
import re
from logging import LogRecord
from pathlib import Path
from typing import Any

try:  # pragma: no cover
    from loggingredactor import CommonPIIRedactingFilter

    _PII_FILTER_CLS: Any = CommonPIIRedactingFilter
except Exception:  # noqa: BLE001
    _PII_FILTER_CLS: Any = None

from .config import get_settings

_OPENROUTER_KEY_RE = re.compile(r"sk-or-[A-Za-z0-9_-]+")
_REDACTED_KEY = "sk-or-[REDACTED]"


def _redact_string(value: str) -> str:
    """Return `value` with any OpenRouter key tokens masked."""
    return _OPENROUTER_KEY_RE.sub(_REDACTED_KEY, value)


class RedactingFilter(logging.Filter):
    """Composable filter: OpenRouter keys + (optional) generic PII.

    Failures inside the filter are swallowed — a logging redaction bug must
    never crash the application.
    """

    def __init__(self) -> None:
        super().__init__()
        pii_filter: logging.Filter | None = None
        if _PII_FILTER_CLS is not None:
            try:
                pii_filter = _PII_FILTER_CLS(silent_failure=True)
            except Exception:  # noqa: BLE001
                pii_filter = None
        self._pii: logging.Filter | None = pii_filter

    def filter(self, record: LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = _redact_string(record.msg)

            args = record.args
            if isinstance(args, tuple):
                record.args = tuple(_redact_string(a) if isinstance(a, str) else a for a in args)
            elif isinstance(args, dict):
                record.args = {
                    k: _redact_string(v) if isinstance(v, str) else v for k, v in args.items()
                }
        except Exception:  # noqa: BLE001
            pass

        if self._pii is not None:
            with contextlib.suppress(Exception):
                self._pii.filter(record)
        return True


class JsonlFormatter(logging.Formatter):
    """One JSON object per line: ``ts``, ``level``, ``logger``, ``message``.

    ``ts`` is rendered as ISO 8601 in UTC. ``exc_info`` is appended only
    when the record carries an exception tuple, so the common case stays a
    flat object that downstream tools (jq, check-jsonschema) can parse.
    """

    def format(self, record: LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(record.created, tz=_dt.UTC)
        payload: dict[str, object] = {
            "ts": ts.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = _redact_string(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def _app_log_dir() -> Path:
    """Return (and create) the JSONL app-log directory."""
    settings = get_settings()
    path = Path(settings.chat_log_dir) / "app"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _today_stamp() -> str:
    """YYYY-MM-DD in UTC — the daily rotation key."""
    return _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d")


_setup_done: bool = False


def setup_logging() -> None:
    """Configure the root logger once. Idempotent.

    Replaces any previously installed root handlers with a single JSONL
    handler backed by a redacting filter, and sets the root level to INFO.
    Third-party loggers are not touched; they will inherit the handler via
    standard logger propagation.
    """
    global _setup_done
    if _setup_done:
        return

    log_file = _app_log_dir() / f"{_today_stamp()}.jsonl"
    handler: logging.Handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(JsonlFormatter())
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]

    _setup_done = True


def get_log_dir() -> Path:
    """Return the configured `chat_log_dir` as a Path, creating it if missing."""
    path = Path(get_settings().chat_log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = [
    "JsonlFormatter",
    "RedactingFilter",
    "get_log_dir",
    "setup_logging",
]
