"""Row loader for ``nba_chatbot_evals_v2.csv`` (EVALS.md §2).

The CSV is the authoritative source of truth for the eval suite: each row
is one scripted conversation, columns are fixed, and the orchestrator
populates the human-verified values (gold_sql, gold_key_values,
expected_tables) in a separate, gated step. This module only READS the
file; it never mutates it.

The loader is intentionally minimal: ``EvalRow`` is a frozen dataclass
whose fields are exactly the CSV's columns (plus a parsed
``acceptable_modes_turn1`` set), and ``load_rows`` is a thin
``csv.DictReader`` wrapper that resolves the default path relative to the
repo root.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


#: Default CSV path. Resolved at call time so tests can monkeypatch
#: this module's ``_DEFAULT_CSV`` if they ever need to point at a
#: fixture file. We walk up from this module until we find a
#: ``plans/arch-overhaul/nba_chatbot_evals_v2.csv`` sibling, so the
#: path resolution is robust to the package's nesting depth.
def _find_default_csv() -> Path:
    here = Path(__file__).resolve().parent
    for ancestor in (here, *here.parents):
        candidate = ancestor / "plans" / "arch-overhaul" / "nba_chatbot_evals_v2.csv"
        if candidate.exists():
            return candidate
    # Fall back to the most likely location (the chat project's plans
    # dir) so the error message is helpful when the file is genuinely
    # missing.
    return here.parent.parent / "plans" / "arch-overhaul" / "nba_chatbot_evals_v2.csv"


_DEFAULT_CSV = _find_default_csv()


@dataclass(frozen=True)
class EvalRow:
    """One row of the eval CSV.

    Every CSV column is exposed as a field so callers can use
    attribute access instead of dict indexing. The only column we
    pre-parse is ``acceptable_modes_turn1`` (pipe-separated -> ``set[str]``)
    because every downstream grader needs set membership; the rest are
    kept as raw strings so we don't lose fidelity when the orchestrator
    starts populating ``expected_tables`` / ``gold_sql``.

    Fields mirror the column order in EVALS.md §2.
    """

    conversation_id: str
    turns: str  # "single" | "multi"
    domain: str
    era: str
    teams_or_players: str
    user_initial_question: str
    expected_answer_mode_turn1: str  # "execute_sql" | "clarify" | "not_answerable"
    acceptable_modes_turn1: set[str] = field(default_factory=set)
    expected_tables: str = ""  # pipe-separated, or TODO_VERIFY until pinned
    gold_sql: str = ""  # human-written; empty until orchestrator populates
    gold_key_values: str = ""  # snapshot from gold_sql; empty until snapshot run
    notes_for_grader: str = ""
    assistant_reply_1: str = ""
    user_followup_1: str = ""
    assistant_reply_2: str = ""
    user_followup_2: str = ""
    assistant_final_action_or_answer: str = ""

    @property
    def scripted_turns(self) -> list[str]:
        """The list of scripted user messages for this row, in order.

        For ``single`` rows this is one element (the initial question).
        For ``multi`` rows this is up to three (initial + followup_1 +
        followup_2) — empty followup cells are skipped so a partial
        multi-turn row doesn't send a blank message.
        """
        messages: list[str] = []
        if self.user_initial_question:
            messages.append(self.user_initial_question)
        if self.user_followup_1:
            messages.append(self.user_followup_1)
        if self.user_followup_2:
            messages.append(self.user_followup_2)
        return messages


def _coerce(value: str | None) -> str:
    """Normalise an optional CSV cell to ``str`` (never ``None``)."""
    return "" if value is None else str(value)


def _parse_acceptable_modes(raw: str) -> set[str]:
    """Pipe-separated modes -> set of trimmed mode tokens.

    Empty / blank input collapses to the empty set (the loader does
    NOT default to anything — a row with no acceptable modes is
    malformed and the grader will treat every mode as a hard fail).
    """
    parts = (raw or "").split("|")
    return {token.strip() for token in parts if token.strip()}


def load_rows(csv_path: Path | str | None = None) -> list[EvalRow]:
    """Load every CSV row as an ``EvalRow``.

    Parameters
    ----------
    csv_path
        Optional override path. Defaults to
        ``chat/plans/arch-overhaul/nba_chatbot_evals_v2.csv`` resolved
        relative to this module.

    Returns
    -------
    list[EvalRow]
        One entry per row, in file order. Empty list if the file is
        missing or has no data rows (header-only).
    """
    path = Path(csv_path) if csv_path is not None else _DEFAULT_CSV
    if not path.exists():
        return []
    rows: list[EvalRow] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            rows.append(
                EvalRow(
                    conversation_id=_coerce(raw.get("conversation_id")),
                    turns=_coerce(raw.get("turns")),
                    domain=_coerce(raw.get("domain")),
                    era=_coerce(raw.get("era")),
                    teams_or_players=_coerce(raw.get("teams_or_players")),
                    user_initial_question=_coerce(raw.get("user_initial_question")),
                    expected_answer_mode_turn1=_coerce(raw.get("expected_answer_mode_turn1")),
                    acceptable_modes_turn1=_parse_acceptable_modes(
                        _coerce(raw.get("acceptable_modes_turn1"))
                    ),
                    expected_tables=_coerce(raw.get("expected_tables")),
                    gold_sql=_coerce(raw.get("gold_sql")),
                    gold_key_values=_coerce(raw.get("gold_key_values")),
                    notes_for_grader=_coerce(raw.get("notes_for_grader")),
                    assistant_reply_1=_coerce(raw.get("assistant_reply_1")),
                    user_followup_1=_coerce(raw.get("user_followup_1")),
                    assistant_reply_2=_coerce(raw.get("assistant_reply_2")),
                    user_followup_2=_coerce(raw.get("user_followup_2")),
                    assistant_final_action_or_answer=_coerce(
                        raw.get("assistant_final_action_or_answer")
                    ),
                )
            )
    return rows


__all__ = ["EvalRow", "load_rows"]
