"""Phase 7 backend observability tests (PLAN §4.1#9, §7.10, §15).

Covers the four Phase-7 deliverables:

* **7-day log retention sweep** (``log_retention``) — old files go,
  new files stay, missing roots don't raise.
* **JSONL schema validation** — committed ``*.schema.json`` files in
  ``chat_tests/fixtures/schemas/`` validate real messages and log
  entries. Provides the pytest-side mirror of ``check-jsonschema``.
* **Redaction audit** — feeds the JSONL formatter + redacting filter
  combinations from ``logging_setup`` and asserts both the
  ``sk-or-...`` token and the ``Authorization: Bearer ...`` form are
  scrubbed from the wire line.
* **Optional OTel hooks** — the ``otel.span`` context manager is a
  true no-op (yields ``None`` and does not import ``opentelemetry``)
  when ``CHAT_OTEL_ENABLED`` is unset.

All tests are file/unit-level — **no warehouse or OpenRouter calls**.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
from pathlib import Path

import jsonschema
import pytest

from chat_server import otel
from chat_server.log_retention import (
    LOG_SUBDIRS,
    RETENTION_DAYS,
    sweep_all,
    sweep_logs,
)
from chat_server.logging_setup import JsonlFormatter, RedactingFilter
from chat_server.sessions import SessionStore

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SCHEMAS_DIR = Path(__file__).resolve().parent / "fixtures" / "schemas"


def _load_schema(name: str) -> dict:
    """Load a committed JSON Schema from ``chat_tests/fixtures/schemas/``."""
    path = _SCHEMAS_DIR / name
    assert path.exists(), f"missing schema: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. 7-day rolling retention sweep
# ---------------------------------------------------------------------------


def _touch(path: Path, mtime: float) -> None:
    """Create an empty file at ``path`` and set its mtime to ``mtime``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_sweep_logs_removes_only_old_files(tmp_path: Path) -> None:
    """Files older than ``retention_days`` are deleted; newer files stay.

    Builds a small tree with three files whose mtimes are pinned to
    ``now - 10d``, ``now - 6h``, and ``now - 1d`` respectively. With
    the default 7-day retention, only the 10-day-old file should be
    unlinked.
    """
    root = tmp_path / "logs"
    now = time.time()
    ten_days_old = root / "queries" / "2025-01-01" / "old.sql"
    six_hours_old = root / "queries" / "2025-06-01" / "recent.sql"
    one_day_old = root / "queries" / "2025-06-09" / "newer.sql"
    _touch(ten_days_old, mtime=now - 10 * 86400)
    _touch(six_hours_old, mtime=now - 6 * 3600)
    _touch(one_day_old, mtime=now - 1 * 86400)

    removed = sweep_logs(root, retention_days=RETENTION_DAYS, now=now)

    assert removed == 1
    assert not ten_days_old.exists(), "10-day-old file should have been swept"
    assert six_hours_old.exists(), "6-hour-old file must remain"
    assert one_day_old.exists(), "1-day-old file must remain"


def test_sweep_logs_removes_empty_leaf_dirs(tmp_path: Path) -> None:
    """After a sweep, empty leaf directories left behind are rmdir'd.

    A date directory whose only files were swept should be removed
    so the log root stays tidy; a directory that still contains files
    must be preserved.
    """
    root = tmp_path / "model"
    stale = root / "2024-01-01" / "old.jsonl"
    fresh = root / "2026-01-01" / "new.jsonl"
    now = time.time()
    _touch(stale, mtime=now - 30 * 86400)
    _touch(fresh, mtime=now - 1 * 86400)

    removed = sweep_logs(root, retention_days=RETENTION_DAYS, now=now)

    assert removed == 1
    assert not stale.exists()
    assert not (root / "2024-01-01").exists(), (
        "empty leaf dir under retention window should have been rmdir'd"
    )
    assert (root / "2026-01-01").exists()
    assert fresh.exists()


def test_sweep_logs_missing_root_returns_zero(tmp_path: Path) -> None:
    """``sweep_logs`` on a nonexistent path returns 0 and does not raise.

    On a fresh checkout (or in a test container) the log root may
    not exist yet; that is not an error.
    """
    missing = tmp_path / "does" / "not" / "exist"
    assert sweep_logs(missing) == 0


def test_sweep_logs_handles_file_instead_of_dir(tmp_path: Path) -> None:
    """A path that exists but is not a directory is skipped (warning only)."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    # Must not raise even though ``root.is_dir()`` is False.
    assert sweep_logs(file_path) == 0


def test_sweep_all_never_raises_on_missing_dir(tmp_path: Path, monkeypatch) -> None:
    """``sweep_all`` returns a subdir→count map even when ``chat_log_dir``
    points at an empty directory.

    Best-effort contract (PLAN §7.10): the lifespan startup must never
    fail because of a missing log root.
    """
    from chat_server import config as config_module

    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    result = sweep_all()
    assert isinstance(result, dict)
    assert set(result.keys()) == set(LOG_SUBDIRS)
    assert all(count == 0 for count in result.values())


def test_sweep_all_returns_counts_per_subdir(tmp_path: Path, monkeypatch) -> None:
    """A populated log root yields the expected per-subdir removal counts."""
    from chat_server import config as config_module

    log_root = tmp_path
    now = time.time()
    # Two stale files in app, one in queries, none in model.
    _touch(log_root / "app" / "2024-01-01.jsonl", mtime=now - 10 * 86400)
    _touch(log_root / "app" / "2024-01-02.jsonl", mtime=now - 10 * 86400)
    _touch(log_root / "queries" / "2024-01-01" / "s" / "t.t.sql", mtime=now - 10 * 86400)
    # A fresh file that must survive.
    _touch(log_root / "queries" / "2026-06-01" / "s" / "t.t.sql", mtime=now - 3600)

    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(log_root))
    result = sweep_all()

    assert result["app"] == 2
    assert result["queries"] == 1
    assert result["model"] == 0
    assert (log_root / "queries" / "2026-06-01" / "s" / "t.t.sql").exists()


# ---------------------------------------------------------------------------
# 2. JSONL schema validation
# ---------------------------------------------------------------------------


def test_session_jsonl_validates_against_schema(tmp_path: Path) -> None:
    """Round-trip a real ``SessionMessage`` and validate the JSONL line.

    Uses ``SessionStore.append_message`` to write the canonical JSONL
    representation, reads the line back, and validates it against the
    committed ``session-message.schema.json``.
    """
    schema = _load_schema("session-message.schema.json")
    store = SessionStore(tmp_path)
    meta = store.create(title="audit")
    store.append_message(meta.id, "user", "hello")
    store.append_message(meta.id, "assistant", "hi back")

    jsonl_path = tmp_path / "sessions" / f"{meta.id}.jsonl"
    lines = [ln for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2

    for line in lines:
        record = json.loads(line)
        jsonschema.validate(instance=record, schema=schema)
        assert record["role"] in {"user", "assistant"}
        assert record["content"]


def test_session_jsonl_schema_rejects_missing_field(tmp_path: Path) -> None:
    """The schema fails closed when a required field is missing."""
    schema = _load_schema("session-message.schema.json")
    bad = {"role": "user", "content": "hi"}  # no `ts`
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_model_log_entry_validates_against_schema() -> None:
    """A hand-built model-log entry matches the committed schema."""
    schema = _load_schema("model-log-entry.schema.json")
    entry = {
        "turn_id": "abc12345",
        "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
        "template_id": "season_thresholds.fifty_forty_ninety",
        "usage": {
            "requests": 1,
            "tool_calls": 0,
            "input_tokens": 123,
            "output_tokens": 45,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "input_audio_tokens": 0,
            "cache_audio_read_tokens": 0,
            "output_audio_tokens": 0,
            "details": {},
        },
    }
    jsonschema.validate(instance=entry, schema=schema)


def test_model_log_entry_schema_allows_error_field() -> None:
    """The optional ``error`` field is permitted (and validated as a string)."""
    schema = _load_schema("model-log-entry.schema.json")
    entry = {
        "turn_id": "deadbeef",
        "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
        "template_id": None,
        "usage": None,
        "error": "TimeoutError: timeout after 300s",
    }
    jsonschema.validate(instance=entry, schema=schema)


def test_model_log_entry_schema_rejects_bad_usage_type() -> None:
    """``usage`` must be an object or null — a bare string is rejected."""
    schema = _load_schema("model-log-entry.schema.json")
    bad = {
        "turn_id": "x",
        "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
        "template_id": None,
        "usage": "not an object",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


# ---------------------------------------------------------------------------
# 3. Redaction audit
# ---------------------------------------------------------------------------


def _build_log_record(msg: str) -> logging.LogRecord:
    """Build a `LogRecord` with ``msg=<msg>``, level=INFO, name='audit'."""
    return logging.LogRecord(
        name="audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=None,
        exc_info=None,
    )


def test_redaction_audit_openrouter_key_in_message() -> None:
    """``sk-or-abc123DEF456`` is replaced by ``sk-or-[REDACTED]``."""
    filt = RedactingFilter()
    formatter = JsonlFormatter()
    record = _build_log_record("token leaked: sk-or-abc123DEF456")
    assert filt.filter(record) is True
    line = formatter.format(record)
    payload = json.loads(line)
    assert payload["message"] == "token leaked: sk-or-[REDACTED]"
    assert "sk-or-abc123DEF456" not in line


def test_redaction_audit_bearer_token() -> None:
    """An ``Authorization: Bearer sk-or-xyz`` line is scrubbed too."""
    filt = RedactingFilter()
    formatter = JsonlFormatter()
    record = _build_log_record("Authorization: Bearer sk-or-xyz")
    assert filt.filter(record) is True
    line = formatter.format(record)
    payload = json.loads(line)
    # The bare key token is gone; the surrounding text remains.
    assert "sk-or-xyz" not in line
    assert "sk-or-[REDACTED]" in payload["message"]
    assert "Authorization: Bearer" in payload["message"]


def test_redaction_audit_redacts_args_when_present() -> None:
    """``record.args`` is scrubbed too (tuple and dict forms)."""
    filt = RedactingFilter()
    # Tuple args.
    record = logging.LogRecord(
        name="audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="prompt=%s",
        args=("sk-or-argsecret",),
        exc_info=None,
    )
    filt.filter(record)
    assert "sk-or-argsecret" not in (record.args[0] if record.args else "")
    assert record.args[0] == "sk-or-[REDACTED]"

    # Dict args: Python's logging layer accepts a dict wrapped in a
    # single-element tuple (the dict arrives at LogRecord as args[0]).
    record = logging.LogRecord(
        name="audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="payload=%(payload)s",
        args=({"payload": "sk-or-dictsecret"},),
        exc_info=None,
    )
    # The RedactingFilter unwraps args[0] when it sees a Mapping, so the
    # post-filter record.args is the bare dict.
    filt.filter(record)
    assert record.args == {"payload": "sk-or-[REDACTED]"}


# ---------------------------------------------------------------------------
# 4. Optional OTel span hooks — off by default
# ---------------------------------------------------------------------------


def test_otel_disabled_when_env_unset() -> None:
    """``otel._ENABLED`` is False unless ``CHAT_OTEL_ENABLED=1``.

    This guards the documented contract: the base install does NOT
    require ``opentelemetry``, and ``CHAT_OTEL_ENABLED`` defaults to
    off. The check is run via subprocess so we exercise the import-time
    branch (the module-level ``_ENABLED`` is captured once at import).
    """
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "from chat_server import otel; print(otel._ENABLED)"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={**os.environ, "CHAT_OTEL_ENABLED": ""},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_otel_span_is_noop_when_disabled() -> None:
    """With OTel disabled, ``otel.span('x')`` yields ``None`` and does not raise.

    Mirrors the constraint from the task: the helper is zero-cost when
    ``CHAT_OTEL_ENABLED`` is unset. The current process inherits the
    test runner's env (no flag set), so we directly assert the
    in-module flag and the runtime no-op behaviour.
    """
    assert otel._ENABLED is False, (
        "otel must be off by default; if this fails, set CHAT_OTEL_ENABLED='' for pytest"
    )
    with otel.span("agent.run", attributes={"session_id": "s1"}) as s:
        # The helper must yield None — no real span object.
        assert s is None
    # Calling again is fine (state should not leak between uses).
    with otel.span("db.execute") as s:
        assert s is None
