"""Optional OpenTelemetry span hooks for the turn pipeline.

"File-first JSONL baseline + optional OTel span hooks." This module
is **off by default** and **must not require
``opentelemetry`` to be installed**.

Enable by setting ``CHAT_OTEL_ENABLED=1`` in the environment and
installing the optional ``otel`` extra::

    uv add --optional chat-server[otel]

When disabled (the default), the public ``span()`` helper is a no-op
context manager — zero imports, zero side effects, no ``opentelemetry``
package needed at runtime. This is enforced by the `_ENABLED` flag
evaluated at module import time:

* If ``CHAT_OTEL_ENABLED`` is not ``"1"`` ⇒ ``_ENABLED=False`` and the
  tracer module is never imported.
* If ``CHAT_OTEL_ENABLED="1"`` but the import fails ⇒ ``_ENABLED=False``
  (logged) and the helper still no-ops cleanly.

When enabled, ``opentelemetry-api`` + ``opentelemetry-sdk`` are imported
lazily inside the guarded block. We do NOT override the host's tracer
provider — if the host has already configured one (e.g. via
``OTEL_TRACES_EXPORTER=otlp`` + an autoinstrumentation entrypoint), we
just acquire a tracer from it and start spans.

Usage in the pipeline::

    with otel.span("agent.run") as span_obj:
        ... do work ...
    with otel.span("db.execute", attributes={"template_id": tid}) as span_obj:
        rows = await db.execute(...)
        if span_obj is not None:
            try:
                span_obj.set_attribute("row_count", len(rows))
            except Exception:  # noqa: BLE001
                pass
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_ENABLED: bool = os.environ.get("CHAT_OTEL_ENABLED", "") == "1"

_tracer: Any = None

if _ENABLED:
    try:
        from opentelemetry import trace as _otel_trace
    except ImportError:
        log.warning(
            "otel: CHAT_OTEL_ENABLED=1 but opentelemetry is not installed; "
            "spans will no-op. Install with `uv add --optional chat-server[otel]`."
        )
        _ENABLED = False
    else:
        _tracer = _otel_trace.get_tracer("chat_server")


@contextlib.contextmanager
def span(name: str, attributes: dict[str, Any] | None = None):
    """Emit an OTel span around the wrapped block.

    Yields the active span (or ``None`` when OTel is disabled). When
    disabled, this is a true no-op: it yields ``None`` and never touches
    any OTel machinery.

    Attribute writes are wrapped in ``try/except`` — a single bad
    attribute value must never crash the wrapped pipeline step.

    Parameters
    ----------
    name:
        Span name (e.g. ``"agent.run"``, ``"db.execute"``).
    attributes:
        Optional dict of attributes to set on the span at creation time.
    """
    if not _ENABLED or _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(name) as s:
        if attributes:
            for key, value in attributes.items():
                with contextlib.suppress(Exception):
                    s.set_attribute(key, value)
        yield s


__all__ = ["span"]
