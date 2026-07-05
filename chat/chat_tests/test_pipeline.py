"""Pipeline tests: validate that ``run_turn`` emits the 11-event union
end-to-end for the canonical 50-40-90 path, plus the three branch
coverage cases (clarification, unknown template, timeout).

All tests use ``pydantic_ai.models.test.TestModel`` to drive the
agent — no live OpenRouter calls happen during ``pytest``. The DB-
backed paths (50-40-90, unknown template) skip when
``data/nba.duckdb`` is unavailable; the timeout path monkeypatches
the executor and therefore doesn't need the warehouse.

Session storage is routed to a temp directory via monkeypatch so test
runs never leak into ``chat/data/sessions/``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator

from pydantic_ai.models.test import TestModel

from chat_server.agent import AgentDeps, _build_agent
from chat_server.db import DuckDBSingleton
from chat_server.events import (
    AnswerFinished,
    ChatError,
    ChatEvent,
    ChatEventUnion,
    Citation,
    ClarificationNeeded,
    IntentClassified,
    TableReady,
    TurnStarted,
)
from chat_server.pipeline import run_turn
from chat_server.schema_context import SchemaContext
from chat_server.sessions import SessionStore
from chat_tests.conftest import skip_no_db

# --- helpers -------------------------------------------------------------


def _make_test_agent(output_args: dict) -> tuple[TestModel, object]:
    """Build a TestModel-backed agent with ``custom_output_args`` set."""
    tm = TestModel(call_tools=[], custom_output_args=output_args)
    agent = _build_agent(tm)
    return tm, agent


def _live_deps() -> AgentDeps:
    """Build deps backed by the real warehouse (used by the happy-path test)."""
    from chat_server.config import get_settings

    settings = get_settings()
    return AgentDeps(
        registry={},
        schema_context=SchemaContext(),
        db=DuckDBSingleton(settings.duckdb_path),
    )


def _event_names(events: list[ChatEvent]) -> list[str]:
    """Convenience: just the `event` discriminator for each emitted event."""
    return [e.event for e in events]


async def _collect(gen: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    """Drain an async generator into a list (test helper)."""
    return [ev async for ev in gen]


# --- 1. happy path: 50-40-90 with all 11 events emitted -----------------


@skip_no_db
def test_pipeline_emits_full_event_sequence(monkeypatch, tmp_path):
    """``run_turn`` produces every applicable ``ChatEvent`` for the
    canonical 50-40-90 template — no error event, at least one row,
    answer mentions Curry, and the model + query logs land on disk.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    # 1. TestModel emits a valid fifty_forty_ninety plan.
    tm, agent = _make_test_agent(
        {
            "template_id": "season_thresholds.fifty_forty_ninety",
            "params": {"min_ppg": 25.0},
        }
    )

    # 2. Patch the pipeline's binding to the agent + deps factory.
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)

    # 3. Redirect the log dir to tmp_path so logs/ isn't polluted.
    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "chat_log_dir", str(tmp_path))

    # 4. Temp session store so we don't write to chat/data/sessions/.
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="50-40-90").id

    # 5. Drain the async generator.
    events = asyncio.run(_collect(run_turn(sid, "50-40-90 with 25 ppg")))

    # 6. Assert the full event sequence.
    names = _event_names(events)
    assert names[0] == "turn_started", names
    assert "intent_classified" in names
    assert "query_started" in names
    assert "query_finished" in names
    assert "table_ready" in names
    assert "reasoning" in names
    # At least one citation is emitted per allowlisted table (ranked_list policy).
    assert names.count("citation") >= 1
    # At least one answer_delta, then exactly one answer_finished.
    assert names.count("answer_delta") >= 1
    assert names.count("answer_finished") == 1
    # No error event on the happy path.
    assert "error" not in names

    # 7. Stronger semantic assertions.
    table_ready = next(e for e in events if isinstance(e, TableReady))
    assert table_ready.row_count >= 1
    assert len(table_ready.rows) >= 1

    answer_finished = next(e for e in events if isinstance(e, AnswerFinished))
    assert "Curry" in answer_finished.answer, answer_finished.answer

    # Citations carry the allowlisted tables.
    citations = [e for e in events if isinstance(e, Citation)]
    cited_tables = {c.table_name for c in citations if c.table_name}
    assert "mart_player_season" in cited_tables

    # Intent classified carries the chosen template + confidence 1.0.
    intent = next(e for e in events if isinstance(e, IntentClassified))
    assert intent.template_id == "season_thresholds.fifty_forty_ninety"
    assert intent.confidence == 1.0

    # 8. Log files were written under tmp_path.
    log_root = tmp_path
    model_files = list((log_root / "model").rglob("*.jsonl"))
    query_dirs = list((log_root / "queries").rglob("*"))
    assert model_files, "expected at least one model JSONL log file"
    assert any(p.is_file() and p.suffix == ".sql" for p in query_dirs), (
        "expected at least one .sql query log file"
    )
    assert any(p.is_file() and p.suffix == ".json" for p in query_dirs), (
        "expected at least one .result.json query log file"
    )

    # 9. Secret redaction: no `sk-or-...` token in any written log file.
    for path in [*model_files, *(p for p in query_dirs if p.is_file())]:
        text = path.read_text(encoding="utf-8")
        assert "sk-or-" not in text, f"API key leaked into log: {path}"


# --- 2. clarification branch: only clarification_needed -----------------


@skip_no_db
def test_pipeline_clarification_path(monkeypatch, tmp_path):
    """When the agent emits a clarification plan, the pipeline emits
    TurnStarted + ClarificationNeeded and nothing else.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    tm, agent = _make_test_agent(
        {
            "template_id": "",
            "params": {},
            "clarification": "Which season should I look at?",
            "not_answerable_note": None,
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="?").id
    events = asyncio.run(_collect(run_turn(sid, "?")))
    names = _event_names(events)

    assert names == ["turn_started", "clarification_needed"], names
    clarification = next(e for e in events if isinstance(e, ClarificationNeeded))
    assert "season" in clarification.question.lower()


# --- 3. unknown template_id: graceful not-answerable answer -------------


@skip_no_db
def test_pipeline_unknown_template_not_answerable(monkeypatch, tmp_path):
    """An unknown template_id degrades to a not-answerable answer
    (no error event), composed via ``compose_not_answerable``.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    tm, agent = _make_test_agent(
        {
            "template_id": "does.not.exist",
            "params": {},
            "clarification": None,
            "not_answerable_note": None,
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="weird").id
    events = asyncio.run(_collect(run_turn(sid, "weird question")))
    names = _event_names(events)

    assert "turn_started" in names
    assert "intent_classified" in names  # we still emit it before the lookup fails
    assert "error" not in names  # graceful, not an error event
    assert "answer_finished" in names

    finished = next(e for e in events if isinstance(e, AnswerFinished))
    assert "does.not.exist" in finished.answer


# --- 4. query timeout: error event with code="query_timeout" -----------


def test_pipeline_timeout_emits_error(monkeypatch, tmp_path):
    """An ``asyncio.TimeoutError`` from the DB run becomes a
    ``ChatError(code="query_timeout", ...)`` event.

    No warehouse needed — we monkeypatch ``get_db().execute`` to raise.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    tm, agent = _make_test_agent(
        {
            "template_id": "season_thresholds.fifty_forty_ninety",
            "params": {"min_ppg": 25.0},
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        # No DB is ever touched because we override ``get_db`` below.
        return AgentDeps(registry={}, schema_context=SchemaContext(), db=None)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))

    # 5x5 timeout ⇒ asyncio.wait_for raises on the agent.run side.
    # Make the in-process DB call sleep long enough that wait_for trips.
    class _SlowDB:
        async def execute(self, sql, params=None, *, limit=None):
            await asyncio.sleep(5)
            raise AssertionError("should have been cancelled")

    monkeypatch.setattr(pipeline_module, "get_db", lambda: _SlowDB())

    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    # Shorten the template's timeout so wait_for fires quickly. We do
    # this by monkeypatching the get_template lookup to return a
    # template with timeout_seconds=0 (the wait_for rejects anything
    # ≤ 0 immediately).  Patching the registry would be more invasive;
    # we patch the resolved Template object the pipeline reads.
    import chat_server.pipeline as pm

    real_get_template = pm.get_template

    def _fast_timeout_get_template(tid: str):
        t = real_get_template(tid)
        # Return a shallow copy with timeout_seconds=0 so wait_for
        # rejects on the first scheduling cycle.
        from dataclasses import replace

        return replace(t, timeout_seconds=0)

    monkeypatch.setattr(pm, "get_template", _fast_timeout_get_template)

    sid = temp_store.create(title="timeout").id
    events = asyncio.run(_collect(run_turn(sid, "force a timeout")))
    names = _event_names(events)

    # We expect TurnStarted → IntentClassified → QueryStarted → ChatError.
    assert "turn_started" in names
    assert "query_started" in names
    err = next((e for e in events if isinstance(e, ChatError)), None)
    assert err is not None, names
    assert err.code == "query_timeout"
    # The answer must NOT be delivered on a timeout.
    assert "answer_finished" not in names


# --- 5. ChatEvent union: round-trip via JSON Schema ---------------------


def test_chat_event_union_round_trip():
    """The 11-event union round-trips through ``model_dump(mode="json")``
    and ``ChatEventUnion.validate_python``.
    """
    e = TurnStarted(session_id="s", turn_id="t", ts=dt.datetime(2025, 1, 1, tzinfo=dt.UTC))
    payload = e.model_dump(mode="json")
    restored = ChatEventUnion.validate_python(payload)
    assert isinstance(restored, TurnStarted)
    assert restored.session_id == "s"


def test_chat_event_union_schema_contains_all_eleven():
    """Every event type is present in the discriminator mapping (this is
    the contract the frontend drift guard depends on).
    """
    schema = ChatEventUnion.json_schema()
    keys = set(schema["discriminator"]["mapping"].keys())
    expected = {
        "turn_started",
        "intent_classified",
        "clarification_needed",
        "query_started",
        "query_finished",
        "table_ready",
        "reasoning",
        "citation",
        "answer_delta",
        "answer_finished",
        "error",
    }
    assert keys == expected, f"missing={expected - keys}, extra={keys - expected}"


# --- 6. secret redaction on the debug log paths (PLAN §7.10) -----------


@skip_no_db
def test_pipeline_does_not_leak_api_key_into_debug_logs(monkeypatch, tmp_path):
    """A user message containing a fake ``sk-or-...`` token must not
    land in any debug-log file under ``queries/`` or ``model/``.

    The visible session history (under ``sessions/``) intentionally
    preserves the user's message verbatim — that's the chat log, not
    a debug artifact. The redaction contract (PLAN §7.10) covers the
    debug paths only.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    tm, agent = _make_test_agent(
        {
            "template_id": "season_thresholds.fifty_forty_ninety",
            "params": {"min_ppg": 25.0},
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="redaction").id
    asyncio.run(
        _collect(run_turn(sid, "sk-or-FAKE-SECRET-TOKEN-12345 was leaked, 50-40-90 with 25 ppg"))
    )

    debug_files = [
        *(tmp_path / "queries").rglob("*"),
        *(tmp_path / "model").rglob("*"),
    ]
    debug_files = [p for p in debug_files if p.is_file()]
    assert debug_files, "expected debug log files to be written"
    leaked = [
        p for p in debug_files if "sk-or-FAKE-SECRET-TOKEN-12345" in p.read_text(encoding="utf-8")
    ]
    assert not leaked, f"API key leaked into debug logs: {leaked}"
