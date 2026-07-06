"""Tests for Stage 3.6: pending-clarification state machine.

This file covers three pieces of the Stage 3.6 wiring:

* ``ClarificationState`` — the Pydantic model that carries the pending
  clarification across turns. Round-trip + staleness + options shape.
* ``build_clarification_context_prefix`` — the deterministic prompt
  prefix the next turn prepends when resolving a clarification.
* ``SessionStore.set_pending_clarification`` /
  ``get_pending_clarification`` /
  ``clear_pending_clarification`` — the parallel JSON store for the
  pending state. Round-trip, missing-file, corrupt-file, stale-file,
  and idempotent-clear tests.

Mirrors the patterns in ``test_history.py`` and ``test_sessions.py``:
``tmp_path``-backed ``SessionStore`` per test, no async fixtures
required, no live warehouse touched.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from chat_server.clarify import (
    DEFAULT_MAX_AGE_SECONDS,
    ClarificationState,
    build_clarification_context_prefix,
)
from chat_server.sessions import SessionStore

# --- Helpers / fixtures -------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    """A tmp_path-backed ``SessionStore`` for IO tests.

    Mirrors the ``store`` fixture in ``test_history.py``. Each test
    gets its own temp directory so the on-disk artefacts are isolated.
    """
    return SessionStore(tmp_path)


def _now() -> _dt.datetime:
    """Timezone-aware UTC now. Centralized for the staleness tests."""
    return _dt.datetime.now(tz=_dt.UTC)


# --- ClarificationState -------------------------------------------------


def test_clarification_state_round_trip_serialization():
    """The model serializes and re-validates cleanly through JSON."""
    state = ClarificationState(
        original_question="Show me Curry's stats",
        clarification_question="Which season?",
        options=["2023-24", "career", "playoffs"],
    )
    roundtripped = ClarificationState.model_validate_json(state.model_dump_json())
    assert roundtripped == state


def test_clarification_state_options_default_none():
    """``options`` defaults to ``None`` so the prefix can omit the
    options clause for free-form clarifications."""
    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
    )
    assert state.options is None


def test_clarification_state_created_at_defaults_to_now():
    """``created_at`` defaults to a fresh UTC timestamp so callers can
    construct a state without thinking about time."""
    before = _now()
    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
    )
    after = _now()

    # ``created_at`` should be between ``before`` and ``after``
    # (allow sub-millisecond drift on monotonic clocks).
    assert before <= state.created_at <= after
    # And it must be timezone-aware (UTC) so the staleness math works.
    assert state.created_at.tzinfo is not None


def test_is_stale_threshold_fresh_is_false():
    """A freshly constructed state is NOT stale — the default 5-minute
    threshold has not elapsed."""
    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
    )
    assert state.is_stale() is False
    assert state.is_stale(max_age_seconds=DEFAULT_MAX_AGE_SECONDS) is False


def test_is_stale_threshold_old_is_true():
    """A state whose ``created_at`` is older than the threshold IS stale."""
    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
        created_at=_now() - _dt.timedelta(minutes=10),
    )
    assert state.is_stale() is True


def test_is_stale_threshold_custom():
    """A custom threshold is respected (both directions)."""
    old_by_one_second = ClarificationState(
        original_question="q",
        clarification_question="cq",
        created_at=_now() - _dt.timedelta(seconds=5),
    )
    # 3-second window → stale.
    assert old_by_one_second.is_stale(max_age_seconds=3) is True
    # 60-second window → fresh.
    assert old_by_one_second.is_stale(max_age_seconds=60) is False


def test_is_stale_disabled_when_max_age_non_positive():
    """``max_age_seconds <= 0`` short-circuits staleness to ``False``
    so the check is trivially disable-able at the call site."""
    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
        created_at=_now() - _dt.timedelta(days=365),
    )
    assert state.is_stale(max_age_seconds=0) is False
    assert state.is_stale(max_age_seconds=-1) is False


# --- build_clarification_context_prefix ---------------------------------


def test_build_clarification_context_prefix_shape_with_options():
    """The prefix includes the original question, the clarification
    question, the options, and the user's reply."""
    state = ClarificationState(
        original_question="Show me Curry's stats",
        clarification_question="Which season?",
        options=["2023-24", "career", "playoffs"],
    )
    prefix = build_clarification_context_prefix(state, "2023-24")

    assert "Show me Curry's stats" in prefix
    assert "Which season?" in prefix
    assert "2023-24" in prefix
    # The reply appears twice (once quoted in the prefix, once as the
    # literal `user_reply` argument echoed back) — at minimum it must
    # appear as the user's reply.
    assert "playoffs" in prefix
    assert "career" in prefix
    # The options list must be present in the prefix (the agent uses
    # it to ground the user's reply against the original menu).
    assert "options" in prefix.lower() or "[" in prefix  # the list literal


def test_build_clarification_context_prefix_shape_without_options():
    """When the state carries no options, the prefix must NOT include
    an options clause — the agent is being asked to handle a free-form
    disambiguation reply."""
    state = ClarificationState(
        original_question="Q",
        clarification_question="CQ",
        options=None,
    )
    prefix = build_clarification_context_prefix(state, "my reply")

    assert "Q" in prefix
    assert "CQ" in prefix
    assert "my reply" in prefix
    # No options clause when there are no options.
    assert "options you offered" not in prefix.lower()


def test_build_clarification_context_prefix_empty_options_treated_as_none():
    """An empty ``options`` list is normalized to ``None`` at the
    boundary so the prefix omits the clause (consistent with the
    pipeline's normalization in the SET hook)."""
    state = ClarificationState(
        original_question="Q",
        clarification_question="CQ",
        options=[],
    )
    prefix = build_clarification_context_prefix(state, "reply")
    assert "options you offered" not in prefix.lower()


def test_build_clarification_context_prefix_is_deterministic():
    """Two calls with the same inputs produce the same prefix — the
    prompt-enrichment contract depends on it for test stability."""
    state = ClarificationState(
        original_question="Q",
        clarification_question="CQ",
        options=["a", "b"],
        # Pin the timestamp so the prefix is bit-for-bit stable across
        # both calls.
        created_at=_dt.datetime(2024, 1, 1, tzinfo=_dt.UTC),
    )
    p1 = build_clarification_context_prefix(state, "r")
    p2 = build_clarification_context_prefix(state, "r")
    assert p1 == p2


def test_build_clarification_context_prefix_marks_followup():
    """The prefix must include a marker phrase so the agent (and any
    downstream log reader) can tell this turn is a clarification
    follow-up rather than a fresh question."""
    state = ClarificationState(
        original_question="Q",
        clarification_question="CQ",
    )
    prefix = build_clarification_context_prefix(state, "r")
    assert "Clarification follow-up" in prefix


# --- SessionStore clarification IO --------------------------------------


def test_clarification_set_get_round_trip(store: SessionStore):
    """``set_pending_clarification`` then ``get_pending_clarification``
    returns equal state.

    Timestamps are excluded from equality: the store serializes and
    re-validates the state, which may introduce sub-millisecond drift
    in the ``created_at`` field (e.g. Pydantic rounding). The
    state-machine semantics do not depend on sub-second precision —
    only on staleness (>= 5 min by default).
    """
    meta = store.create(title="rt")
    state = ClarificationState(
        original_question="Show me Curry's stats",
        clarification_question="Which season?",
        options=["2023-24", "career"],
    )

    store.set_pending_clarification(meta.id, state)

    loaded = store.get_pending_clarification(meta.id)
    assert loaded is not None
    assert loaded.original_question == state.original_question
    assert loaded.clarification_question == state.clarification_question
    assert loaded.options == state.options
    # Timestamps land within a small window around ``state.created_at``.
    delta = abs((loaded.created_at - state.created_at).total_seconds())
    assert delta < 1.0, f"timestamp drift {delta}s exceeds 1s tolerance"


def test_clarification_get_missing_returns_none(store: SessionStore):
    """``get_pending_clarification`` returns ``None`` when no file has
    been written — both for an unknown session and for a session
    whose pending state was cleared."""
    # Unknown session id: no metadata, no pending state.
    assert store.get_pending_clarification("does-not-exist") is None

    # A real session that never had a pending clarification.
    meta = store.create(title="no-pending")
    assert store.get_pending_clarification(meta.id) is None


def test_clarification_get_corrupt_returns_none_and_clears(store: SessionStore):
    """A present-but-corrupt clarification file is treated as "no
    pending" and best-effort cleared so the corruption does not recur
    on every subsequent turn."""
    meta = store.create(title="corrupt")
    path = store._clarify_path(meta.id)
    path.write_text("not valid json{{{", encoding="utf-8")

    assert store.get_pending_clarification(meta.id) is None
    # Best-effort cleanup removed the file.
    assert not path.exists()


def test_clarification_get_invalid_schema_returns_none_and_clears(
    store: SessionStore,
):
    """A file that parses as JSON but does not validate against
    ``ClarificationState`` (missing required field, wrong type) is
    treated as corrupt and best-effort cleared."""
    meta = store.create(title="bad-schema")
    path = store._clarify_path(meta.id)
    # Missing both required fields (`original_question`,
    # `clarification_question`); valid JSON, invalid model.
    path.write_text('{"options": ["a", "b"]}', encoding="utf-8")

    assert store.get_pending_clarification(meta.id) is None
    assert not path.exists()


def test_clarification_get_stale_returns_none_and_clears(store: SessionStore):
    """A pending clarification older than the staleness threshold is
    discarded on read so the user is not trapped in a stale clarify
    loop. The file is best-effort cleared as a side-effect."""
    meta = store.create(title="stale")
    path = store._clarify_path(meta.id)

    # 10 minutes old — well past the 5-minute default threshold.
    stale = ClarificationState(
        original_question="earlier question",
        clarification_question="earlier clarify?",
        options=None,
        created_at=_now() - _dt.timedelta(minutes=10),
    )
    store.set_pending_clarification(meta.id, stale)

    assert store.get_pending_clarification(meta.id) is None
    # File is best-effort cleared.
    assert not path.exists()


def test_clarification_get_stale_options_have_no_effect_on_staleness(
    store: SessionStore,
):
    """Staleness is driven solely by ``created_at`` — the presence or
    absence of options does not change the threshold."""
    meta = store.create(title="stale-with-options")
    stale = ClarificationState(
        original_question="earlier question",
        clarification_question="earlier clarify?",
        options=["x", "y"],
        created_at=_now() - _dt.timedelta(minutes=10),
    )
    store.set_pending_clarification(meta.id, stale)

    assert store.get_pending_clarification(meta.id) is None


def test_clarification_clear_idempotent(store: SessionStore):
    """``clear_pending_clarification`` is idempotent: clearing when no
    file exists raises nothing and does not create one."""
    # Never had a pending clarification: clearing is a no-op.
    meta = store.create(title="clear-empty")
    store.clear_pending_clarification(meta.id)  # must not raise

    path = store._clarify_path(meta.id)
    assert not path.exists()


def test_clarification_clear_removes_existing(store: SessionStore):
    """``clear_pending_clarification`` removes an existing pending file."""
    meta = store.create(title="clear-existing")
    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
    )
    store.set_pending_clarification(meta.id, state)

    path = store._clarify_path(meta.id)
    assert path.exists()

    store.clear_pending_clarification(meta.id)
    assert not path.exists()


def test_clarification_set_uses_atomic_write(store: SessionStore, tmp_path: Path):
    """``set_pending_clarification`` writes via the ``.tmp`` +
    ``os.replace`` pattern — no half-written ``.tmp`` lingers after
    a successful write."""
    meta = store.create(title="atomic")
    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
    )
    store.set_pending_clarification(meta.id, state)

    target = tmp_path / "sessions" / f"{meta.id}.clarify.json"
    assert target.exists()

    # No leftover ``.tmp`` sibling.
    leftover = list(tmp_path.glob(f"sessions/.{meta.id}.clarify.json.tmp"))
    assert leftover == [], f"stale tmp files: {leftover}"


def test_clarification_set_overwrites_previous(store: SessionStore):
    """A second ``set_pending_clarification`` overwrites the first
    state — the pending clarification is a single document, not a
    stream."""
    meta = store.create(title="overwrite")

    first = ClarificationState(
        original_question="first q",
        clarification_question="first cq",
    )
    second = ClarificationState(
        original_question="second q",
        clarification_question="second cq",
    )

    store.set_pending_clarification(meta.id, first)
    store.set_pending_clarification(meta.id, second)

    loaded = store.get_pending_clarification(meta.id)
    assert loaded is not None
    assert loaded.original_question == "second q"
    assert loaded.clarification_question == "second cq"


def test_clarification_round_trip_with_empty_options_normalized(
    store: SessionStore,
):
    """The pipeline / route normalize an empty options list to
    ``None`` at the SET hook; the store accepts either form (the
    normalization is a caller-side concern)."""
    meta = store.create(title="empty-opts")

    state = ClarificationState(
        original_question="q",
        clarification_question="cq",
        options=[],
    )
    store.set_pending_clarification(meta.id, state)

    loaded = store.get_pending_clarification(meta.id)
    assert loaded is not None
    assert loaded.options == []  # empty list round-trips intact

    # And the build_clarification_context_prefix helper treats the
    # empty list as "no options" — covered separately above, but
    # re-asserted here against the round-tripped state for symmetry.
    prefix = build_clarification_context_prefix(loaded, "r")
    assert "options you offered" not in prefix.lower()


# --- Integration: helper-level two-turn resolution ---------------------


def test_clarification_two_turn_resolution_via_helpers(
    store: SessionStore,
    tmp_path: Path,
):
    """End-to-end state-machine flow at the helper level (no live
    agent). Turn 1: set a pending clarification. Turn 2: load it,
    render the enriched prefix, then clear it after producing a
    non-clarify plan. The helper-level test exercises the three
    integration points (enrich / set / clear) without standing up
    the full pipeline / route."""
    meta = store.create(title="two-turn")

    # --- Turn 1: agent produces clarify -> set pending. ---------------
    pending = ClarificationState(
        original_question="Show me Curry's season stats",
        clarification_question="Which season?",
        options=["2023-24", "career"],
    )
    store.set_pending_clarification(meta.id, pending)

    # --- Turn 2: load pending, render enriched prefix. ----------------
    loaded = store.get_pending_clarification(meta.id)
    assert loaded is not None
    enriched = build_clarification_context_prefix(loaded, "2023-24")

    # The enriched prompt includes every piece the agent needs to
    # ground its plan back to the original question.
    assert "Show me Curry's season stats" in enriched
    assert "Which season?" in enriched
    assert "2023-24" in enriched
    assert "career" in enriched
    assert "Clarification follow-up" in enriched

    # --- Turn 2: agent produces a non-clarify plan -> clear pending. ---
    store.clear_pending_clarification(meta.id)

    # Next turn: no enrichment.
    after = store.get_pending_clarification(meta.id)
    assert after is None
