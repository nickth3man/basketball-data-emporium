"""Pending-clarification state machine (Stage 3.6).

Complements the Stage 3.5 model-history persistence. While 3.5 keeps the
Pydantic AI ``ModelMessage`` history intact across turns, 3.6 keeps a
*side-channel* alive across turns: when the agent ends a turn in
a ``ClarifyPlan``, the structured disambiguation is persisted to
disk; on the next turn the prompt is enriched so the agent knows the
user is answering a clarification (not asking a fresh question).

Designed to work even when the model-history snapshot is absent or
corrupt — the two stores are independent (different files, different
read paths).

All IO is best-effort; the ``SessionStore`` getters return ``None`` on
any failure (absent file, corrupt JSON, validation error, OSError, or
stale timestamp) and the setters may raise — callers wrap them in
try/except so a permissions error never breaks a turn.
"""

from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, Field

DEFAULT_MAX_AGE_SECONDS = 300


class ClarificationState(BaseModel):
    """Pending clarification for one session.

    Set when the agent ends a turn in ``clarify`` mode; cleared the
    next time a non-clarify plan is produced. The state carries enough
    context for the next turn to reconstruct what was being
    disambiguated and what the agent originally asked, so the enriched
    prompt can re-frame the user's literal reply back onto the original
    question.

    Fields
    ------
    original_question
        The raw user message of the turn that triggered the
        clarification. Captured so the next turn's enriched prompt can
        remind the agent of the *original* ask (not the disambiguation
        reply itself).
    clarification_question
        The question text the agent emitted on the
        ``Clarification`` model — the ``Clarification.question`` field
        verbatim.
    options
        Optional disambiguation options the agent emitted
        (``Clarification.options``). ``None`` when the agent had no
        canonical list to offer; an empty list is normalized to
        ``None`` at the boundary.
    created_at
        UTC timestamp at which this state was persisted. Stale
        states (older than :meth:`is_stale`'s threshold) are discarded
        on read so an abandoned clarify doesn't trap the user.
    """

    original_question: str
    clarification_question: str
    options: list[str] | None = None
    created_at: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now(tz=_dt.UTC))

    def is_stale(self, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> bool:
        """Return True if ``created_at`` is older than ``max_age_seconds``.

        Pure function (uses ``datetime.now(UTC)``). Callers use this to
        discard abandoned clarifications rather than trapping a user
        in a stale clarify loop.

        ``max_age_seconds <= 0`` short-circuits to ``False`` so a
        disabled staleness check is trivial to express at the call
        site (no need for a separate "is the check on?" flag).
        """
        if max_age_seconds <= 0:
            return False
        now = _dt.datetime.now(tz=_dt.UTC)
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=_dt.UTC)
        age = (now - created).total_seconds()
        return age > max_age_seconds


def build_clarification_context_prefix(
    state: ClarificationState,
    user_reply: str,
) -> str:
    """Render the enrichment prefix prepended to the agent's prompt.

    The prefix tells the agent three things: (1) this turn is a
    clarification follow-up (not a fresh question), (2) what the user
    originally asked, and (3) what the agent asked for plus the
    options it offered. The user's literal reply is included verbatim
    so the agent can act on it directly.

    Plain text, no markdown flourish — the system prompt is the right
    place for structured instructions; this prefix is contextual
    metadata the agent reasons over.

    The ``options`` clause is omitted when the state carries no
    canonical list (``state.options is None`` or empty), keeping the
    prefix concise for free-form clarifications.
    """
    options_clause = ""
    if state.options:
        options_clause = f" The options you offered were: {state.options}."
    return (
        f"[Clarification follow-up] The user's earlier question was: "
        f'"{state.original_question}". '
        f'You asked for clarification: "{state.clarification_question}".'
        f"{options_clause} "
        f'The user has now replied: "{user_reply}". '
        f"Using this reply, produce a complete plan for the original question."
    )


__all__ = [
    "ClarificationState",
    "build_clarification_context_prefix",
    "DEFAULT_MAX_AGE_SECONDS",
]
