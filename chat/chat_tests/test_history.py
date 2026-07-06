"""Tests for Stage 3.5: full Pydantic AI ``ModelMessage`` history persistence.

This file covers three pieces of the Stage 3.5 wiring:

* ``keep_last_messages_with_tools`` — the pure trimmer that backs off
  its cut point whenever the boundary would orphan a
  ``ToolReturnPart`` from its preceding ``ToolCallPart`` (or vice
  versa). One targeted test exercises the pair-keeping invariant
  directly.
* ``SessionStore.append_model_history`` / ``SessionStore.load_model_history`` —
  the parallel JSONL store for the model transcript. Round-trip,
  missing-file, and (at the helper level) corrupt-file tests.
* ``_load_model_history`` — the pipeline/route helper that ties the
  store + the pydantic-ai validator + the trimmer together. Tested for
  graceful degradation when the persisted history can't be re-validated.

These tests intentionally stay off the live warehouse — they're about
IO + pure-function behavior, not the agent's runtime semantics. The
integration test ``test_corrupt_history_degrades_to_empty`` exercises
``_load_model_history`` directly (without going through the full
``run_turn``/``chat`` pipeline) so the test stays fast and doesn't
require a TestModel wiring.

Mirrors the patterns in ``test_sessions.py``: ``tmp_path``-backed
``SessionStore`` per test, no async fixtures required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from chat_server.agent import keep_last_messages_with_tools
from chat_server.pipeline import _load_model_history
from chat_server.sessions import SessionStore

# --- Helpers -------------------------------------------------------------


def _user(content: str) -> ModelRequest:
    """Build a one-part user prompt."""
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant_text(text: str) -> ModelResponse:
    """Build a one-part assistant text response."""
    return ModelResponse(parts=[TextPart(content=text)])


def _assistant_tool_call(tool_name: str, args: dict, call_id: str) -> ModelResponse:
    """Build an assistant response that calls a tool."""
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id)],
    )


def _tool_return(tool_name: str, content: str, call_id: str) -> ModelRequest:
    """Build a user-side tool return."""
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=call_id)],
    )


# --- keep_last_messages_with_tools --------------------------------------


def test_keep_last_messages_with_tools_preserves_pairs():
    """Pair-keeping invariant: trim must not leave a ToolReturnPart
    without its preceding ToolCallPart in the kept window.

    We build a conversation with a tool call/return pair that straddles
    the naive ``[-n:]`` cut point, then assert the trimmer backed the
    cut up so both halves of the pair survive.
    """
    # 25 messages: 10 user prompts (ModelRequest), then a tool sequence
    # (call → return → text), then 12 more mixed messages.
    msgs = []
    for i in range(10):
        msgs.append(_user(f"prompt {i}"))
    msgs.append(_assistant_tool_call("lookup_player", {"name": "Curry"}, "call-1"))
    msgs.append(_tool_return("lookup_player", "Stephen Curry", "call-1"))
    msgs.append(_assistant_text("Top result: Stephen Curry"))
    for i in range(11):
        msgs.append(_user(f"followup {i}"))
    msgs.append(_assistant_text("done"))

    assert len(msgs) == 25

    # Keep last 20. Naive cut = 5 → keeps msgs[5:] = everything from the
    # tool CALL onward, but the tool CALL's return at index 6 is also
    # there. That's fine for this synthetic input — pair intact.

    # Now build a case where the pair IS cleaved by the naive cut:
    # place the pair straddling the boundary at index 13/14 with n=10.
    msgs = []
    msgs.append(_assistant_text("greeting"))  # 0
    for i in range(11):
        msgs.append(_user(f"q{i}"))  # 1..11
    msgs.append(_assistant_tool_call("lookup_team", {"name": "LAL"}, "call-99"))  # 12
    msgs.append(_tool_return("lookup_team", "Los Angeles Lakers", "call-99"))  # 13
    msgs.append(_assistant_text("Lakers found"))  # 14
    msgs.append(_user("thanks"))  # 15

    # n=4 → naive cut at index 12 → keeps msgs[12:] = [call, return, text, thanks].
    # Pair intact, no orphan. Use this to assert the trivial path.

    trimmed = keep_last_messages_with_tools(msgs, n=4)
    assert len(trimmed) == 4
    assert trimmed[0] is msgs[12]  # call
    assert trimmed[1] is msgs[13]  # return

    # Now construct the failure case: n=3 cuts between call (index 12)
    # and return (index 13), leaving the ToolReturnPart orphaned.
    msgs2 = []
    msgs2.append(_assistant_text("greeting"))  # 0
    for i in range(11):
        msgs2.append(_user(f"q{i}"))  # 1..11
    msgs2.append(_assistant_tool_call("lookup_team", {"name": "LAL"}, "call-99"))  # 12
    msgs2.append(_tool_return("lookup_team", "Los Angeles Lakers", "call-99"))  # 13
    msgs2.append(_assistant_text("Lakers found"))  # 14
    msgs2.append(_user("thanks"))  # 15

    trimmed2 = keep_last_messages_with_tools(msgs2, n=3)
    # Naive cut at 13 would give [return, text, thanks] — orphaned return.
    # The trimmer MUST back up to 12 so the pair stays intact.
    assert len(trimmed2) == 4, "trimmer must extend the window to keep the pair"
    assert trimmed2[0] is msgs2[12], "kept window must include the ToolCallPart"
    # Confirm a ToolReturnPart at the head of the trimmed window has its
    # matching ToolCallPart in the kept window too.
    first_return = trimmed2[1].parts[0]
    assert isinstance(first_return, ToolReturnPart)
    # The matching call is at trimmed2[0].
    matching_call = trimmed2[0].parts[0]
    assert isinstance(matching_call, ToolCallPart)
    assert matching_call.tool_call_id == first_return.tool_call_id


def test_keep_last_messages_with_tools_short_input_unchanged():
    """When the input is shorter than ``n``, the input is returned as-is."""
    msgs = [_user("hi"), _assistant_text("hello")]
    out = keep_last_messages_with_tools(msgs, n=20)
    assert out == msgs
    assert out is not msgs  # defensive copy


def test_keep_last_messages_with_tools_pure_last_n_when_no_pairs():
    """No tool parts in the window → behaves like a plain ``[-n:]`` slice."""
    msgs = [_user(f"q{i}") for i in range(30)]
    out = keep_last_messages_with_tools(msgs, n=5)
    assert len(out) == 5
    assert out == msgs[-5:]


def test_keep_last_messages_with_tools_handles_version_drift_defensively():
    """If the part types can't be detected (e.g. renamed in a future
    pydantic-ai release), the trimmer should still return a sane
    last-``n`` slice rather than raising.

    We simulate version drift by passing a list of stand-in objects
    that don't subclass ``ToolCallPart`` / ``ToolReturnPart``. The
    ``getattr(msg, "parts", ())`` guard in the helper means a missing
    ``.parts`` attribute also degrades to a plain slice.
    """

    class _Standin:
        def __init__(self, idx: int) -> None:
            self.idx = idx

    standins = [_Standin(i) for i in range(25)]
    out = keep_last_messages_with_tools(standins, n=10)  # type: ignore[arg-type]
    assert len(out) == 10
    assert [s.idx for s in out] == list(range(15, 25))


# --- SessionStore model-history IO --------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    """A tmp_path-backed ``SessionStore`` for IO tests.

    Mirrors the ``temp_store`` fixture in ``test_sessions.py`` but does
    NOT monkeypatch the routes layer — these tests use the store
    directly via its public API.
    """
    return SessionStore(tmp_path)


def _serialized_messages(msgs: list) -> bytes:
    """Round-trip a list of ``ModelMessage`` through JSON, mirroring what
    ``result.all_messages_json()`` returns to ``append_model_history``."""
    return ModelMessagesTypeAdapter.dump_json(msgs)


def test_load_append_round_trip(store: SessionStore):
    """``append_model_history`` then ``load_model_history`` returns the
    same payload."""
    meta = store.create(title="rt")
    msgs = [
        _user("hi"),
        _assistant_text("hello"),
        _user("who scored the most?"),
        _assistant_tool_call("lookup_player", {"name": "Curry"}, "call-1"),
        _tool_return("lookup_player", "Stephen Curry", "call-1"),
        _assistant_text("Top result: Stephen Curry"),
    ]
    payload = _serialized_messages(msgs)

    store.append_model_history(meta.id, payload)

    loaded = store.load_model_history(meta.id)
    # Round-tripped through JSON, the parsed list must match what we
    # serialized (model_dump equality, not identity).
    assert isinstance(loaded, list)
    assert len(loaded) == len(msgs)

    # Re-validate through the adapter to prove the on-disk format is
    # still parseable by pydantic-ai on the next turn.
    revalidated = ModelMessagesTypeAdapter.validate_python(loaded)
    assert len(revalidated) == len(msgs)


def test_load_append_overwrites_per_turn(store: SessionStore):
    """A second ``append_model_history`` call overwrites the first
    snapshot — the model history is per-turn, not append-per-message.
    """
    meta = store.create(title="overwrite")

    store.append_model_history(meta.id, _serialized_messages([_user("a")]))
    store.append_model_history(meta.id, _serialized_messages([_user("a"), _assistant_text("b")]))

    loaded = store.load_model_history(meta.id)
    assert len(loaded) == 2
    # Last entry should be the assistant text, not the user prompt.
    assert loaded[1]["parts"][0]["part_kind"] == "text"


def test_append_uses_atomic_write(store: SessionStore, tmp_path: Path):
    """The ``.tmp`` sibling is cleaned up after a successful write so it
    doesn't accumulate as junk across turns.
    """
    meta = store.create(title="atomic")
    store.append_model_history(meta.id, _serialized_messages([_user("hi")]))

    target = tmp_path / "sessions" / f"{meta.id}.model.jsonl"
    assert target.exists()

    # The hidden .tmp sibling should not survive a successful write.
    leftover = list(tmp_path.glob(f"sessions/.{meta.id}.model.jsonl.tmp"))
    assert leftover == [], f"stale tmp files: {leftover}"


def test_load_missing_returns_empty(store: SessionStore):
    """``load_model_history`` on an unknown session returns ``[]``."""
    # Session was never created; no file on disk.
    assert store.load_model_history("does-not-exist") == []

    # Also true for a session that exists but never had a snapshot.
    meta = store.create(title="no-history")
    assert store.load_model_history(meta.id) == []


def test_load_empty_file_returns_empty(store: SessionStore):
    """A zero-byte snapshot file (touch()ed but never written) returns
    ``[]`` rather than raising — the store treats it as absent."""
    meta = store.create(title="empty")
    (store._root / f"{meta.id}.model.jsonl").touch()  # zero bytes

    assert store.load_model_history(meta.id) == []


def test_load_corrupt_raises(store: SessionStore):
    """A present-but-corrupt snapshot raises so the caller can degrade.

    The pipeline/route wrappers catch the exception and fall back to an
    empty history; here we only assert the store surfaces the failure
    rather than swallowing it.
    """
    meta = store.create(title="corrupt")
    (store._root / f"{meta.id}.model.jsonl").write_text("not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        store.load_model_history(meta.id)


def test_load_non_list_payload_raises(store: SessionStore):
    """A JSON file that parses but isn't a list (e.g. an object) raises
    ``ValueError`` so the caller can degrade."""
    meta = store.create(title="non-list")
    (store._root / f"{meta.id}.model.jsonl").write_text('{"kind": "oops"}', encoding="utf-8")

    with pytest.raises(ValueError, match="not a JSON array"):
        store.load_model_history(meta.id)


# --- _load_model_history (the wiring helper) ----------------------------


def test_load_model_history_returns_empty_when_no_snapshot(store: SessionStore):
    """``_load_model_history`` for an unknown session returns ``[]``."""
    assert _load_model_history(store, "missing-sid") == []


def test_load_model_history_validates_and_trims(store: SessionStore):
    """End-to-end through the helper: serialize → store → load → validate
    → trim. The trimmer drops everything older than the last 20, but
    keeps tool pairs intact.
    """
    meta = store.create(title="wiring")

    # 30 messages: half user prompts, half assistant texts. No tool
    # parts — keeps the assertion focused on the trim step.
    msgs: list = []
    for i in range(15):
        msgs.append(_user(f"q{i}"))
        msgs.append(_assistant_text(f"a{i}"))
    assert len(msgs) == 30

    store.append_model_history(meta.id, _serialized_messages(msgs))

    loaded = _load_model_history(store, meta.id)
    assert len(loaded) == 20
    # Last message in the input should be the last in the trimmed output.
    # Equality (not identity) because the messages round-trip through
    # JSON and get re-validated by ModelMessagesTypeAdapter — the
    # timestamp tzinfo may be a fresh TzInfo(0) on the deserialized copy.
    revalidated = ModelMessagesTypeAdapter.validate_python(loaded)
    assert revalidated[-1].parts[0].content == msgs[-1].parts[0].content


def test_corrupt_history_degrades_to_empty(store: SessionStore):
    """Stage 3.5 robustness contract: a corrupt history file MUST
    degrade to ``[]`` rather than crashing the turn.

    Tested at the ``_load_model_history`` helper level (the pipeline /
    route callers wrap this helper in try/except via the helper's own
    internal guard). The helper itself never raises — it returns
    ``[]`` and logs a warning.
    """
    meta = store.create(title="bad")
    (store._root / f"{meta.id}.model.jsonl").write_text("garbage{{{not json", encoding="utf-8")

    # Helper must NOT raise.
    assert _load_model_history(store, meta.id) == []


def test_corrupt_history_after_valid_payload_degrades_to_empty(store: SessionStore):
    """A subsequent turn that overwrites the snapshot with garbage (e.g.
    a pydantic-ai schema-drift) must still degrade gracefully — the
    helper does NOT consult the previously good payload.
    """
    meta = store.create(title="schema-drift")

    # First turn: valid payload.
    store.append_model_history(meta.id, _serialized_messages([_user("a")]))
    assert _load_model_history(store, meta.id) != []

    # Second turn: a different writer (e.g. a downgraded pydantic-ai)
    # overwrites with a payload that fails re-validation.
    bad_payload = json.dumps([{"this": "is not a ModelMessage shape"}]).encode("utf-8")
    store.append_model_history(meta.id, bad_payload)

    # Helper degrades to empty rather than crashing the next turn.
    assert _load_model_history(store, meta.id) == []
