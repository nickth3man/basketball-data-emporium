"""Pipeline coverage for the v2 discriminated-plan cascade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from pydantic_ai.models.test import TestModel

from chat_server.agent import AgentDeps, _build_agent
from chat_server.db import DuckDBSingleton
from chat_server.events import AnswerFinished, ChatError, ChatEvent, ClarificationNeeded
from chat_server.pipeline import run_turn
from chat_server.schema_context import SchemaContext
from chat_server.sessions import SessionStore


async def _collect(gen: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    return [event async for event in gen]


def _patch_turn(monkeypatch, tmp_path, output: dict) -> SessionStore:
    """Inject a TestModel and only the public dependencies run_turn needs."""
    from chat_server import config, pipeline

    agent = _build_agent(TestModel(call_tools=[], custom_output_args=output))
    monkeypatch.setattr(pipeline, "get_agent", lambda: agent)

    async def make_deps() -> AgentDeps:
        # These plans must return before touching the DB.
        return AgentDeps(schema_context=SchemaContext(), db=cast(DuckDBSingleton, None))

    monkeypatch.setattr(pipeline, "make_deps", make_deps)
    monkeypatch.setattr(config.get_settings(), "chat_log_dir", str(tmp_path))
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline, "get_store", lambda: store)
    return store


def test_pipeline_clarify_plan_stops_before_query(monkeypatch, tmp_path):
    store = _patch_turn(
        monkeypatch,
        tmp_path,
        {
            "answer_mode": "clarify",
            "question_interpretation": "The requested season is ambiguous.",
            "clarification": {"question": "Which season should I use?", "options": ["2023-24"]},
        },
    )

    session_id = store.create(title="clarify").id
    events = asyncio.run(_collect(run_turn(session_id, "leaders")))

    assert [event.event for event in events] == ["turn_started", "clarification_needed"]
    clarification = next(event for event in events if isinstance(event, ClarificationNeeded))
    assert clarification.question == "Which season should I use?"
    assert clarification.options == ["2023-24"]


def test_pipeline_not_answerable_plan_streams_grounded_note(monkeypatch, tmp_path):
    store = _patch_turn(
        monkeypatch,
        tmp_path,
        {
            "answer_mode": "not_answerable",
            "question_interpretation": "This asks for data outside the warehouse.",
            "not_answerable_note": "The warehouse does not include college statistics.",
        },
    )

    session_id = store.create(title="out of scope").id
    events = asyncio.run(_collect(run_turn(session_id, "college stats")))

    assert "error" not in [event.event for event in events]
    finished = next(event for event in events if isinstance(event, AnswerFinished))
    assert finished.answer == "The warehouse does not include college statistics."


def test_pipeline_agent_failure_becomes_error_event(monkeypatch, tmp_path):
    from chat_server import config, pipeline

    class BrokenAgent:
        async def run(self, *args, **kwargs):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(pipeline, "get_agent", lambda: BrokenAgent())

    async def make_deps() -> AgentDeps:
        return AgentDeps(schema_context=SchemaContext(), db=cast(DuckDBSingleton, None))

    monkeypatch.setattr(pipeline, "make_deps", make_deps)
    monkeypatch.setattr(config.get_settings(), "chat_log_dir", str(tmp_path))
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline, "get_store", lambda: store)

    session_id = store.create(title="failure").id
    events = asyncio.run(_collect(run_turn(session_id, "anything")))

    error = next(event for event in events if isinstance(event, ChatError))
    assert error.code == "agent_failed"
