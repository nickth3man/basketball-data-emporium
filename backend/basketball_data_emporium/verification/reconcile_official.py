"""SCAFFOLD: cross-source reconciliation against official NBA endpoints.

This module is the Layer 5 deliverable described in
``ideas/data-verification-methodology.md`` §8 (and §8.2 in particular — the
"Official endpoint → warehouse table reconciliation matrix"). It is a
**scaffold**: the endpoint map, the normalization helpers, the column
maps, and the ``reconcile()`` diff function are all production-shaped
and unit-testable without any network I/O or DuckDB. The actual
``nba_api`` HTTP plumbing is dispatched via
``get_official_fetcher()`` only when the optional ``[reconcile]`` extra
is installed and the caller opts in.

Design notes (matching §8.3 of the methodology):

* The ``reconcile()`` function takes an **injected** ``fetcher`` callable
  so tests can run offline against a planted row set.
* Tolerances default to ``0`` (counting ints are exact), ``0.001`` for
  percentages per §8.3.
* Column-case normalization is handled at lookup time: field names
  passed in are the warehouse (lowercase) names, and the fetcher's
  UPPERCASE ``nba_api`` keys are resolved via the column maps below.
* Discrepancy records are dicts shaped like ``audit.metric_discrepancy``:
  ``{entity, field, expected, actual, severity}``.

Status of endpoint wiring (issue #9)
-----------------------------------

The CLI runs all endpoints in :data:`ENDPOINT_MAP`. As of this revision:

* ``dim_player`` is fully wired via ``CommonAllPlayers`` — that endpoint
  is invoked, the response is normalized to row-dicts with
  ``nba_api``'s UPPERCASE keys, and the rows are compared against
  ``unified_star.dim_player`` on ``player_id``.
* The remaining eight endpoints raise
  ``NotImplementedError("<endpoint> not wired yet; see issue #9")``
  with a ``# TODO(issue-9)`` comment — the harness plumbing is in
  place (retry, rate-limit, report writing, exit codes) but each
  endpoint needs its own kwargs + response->row normalization.

The CLI distinguishes three outcomes:

* clean run (no discrepancies) -> exit 0
* HIGH/CRITICAL discrepancies present -> exit 1
* run with only stubbed endpoints (or a dry-run) -> exit 0

This is the behavior the nightly workflow relies on: a stub-only run
does not fail the nightly; a real diff does.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# §8.2 Official endpoint -> warehouse table reconciliation matrix.
#
# Each entry is keyed by the warehouse object name and contains the
# official ``nba_api`` endpoint(s) plus the columns to diff. ``grain``
# records the natural key of a row (used to keep the matrix readable;
# the actual key field for ``reconcile()`` is provided by the caller).
# ---------------------------------------------------------------------------
ENDPOINT_MAP: dict[str, dict[str, Any]] = {
    "dim_player": {
        "endpoint": "CommonAllPlayers / CommonPlayerInfo",
        "grain": "player",
        "key_fields": [
            "PERSON_ID",
            "DISPLAY_FIRST_LAST",
            "BIRTHDATE",
            "DRAFT_YEAR",
            "DRAFT_ROUND",
            "DRAFT_NUMBER",
            "FROM_YEAR",
            "TO_YEAR",
            "ROSTERSTATUS",
        ],
    },
    "dim_game": {
        "endpoint": "LeagueGameLog",
        "grain": "game",
        "key_fields": [
            "GAME_DATE",
            "MATCHUP",
            "WL",
            "MIN",
            "PTS",
            "SEASON_ID",
        ],
    },
    "fact_player_game_boxscore": {
        "endpoint": "BoxScoreTraditionalV2",
        "grain": "player-game",
        "key_fields": [
            "MIN",
            "PTS",
            "FGM", "FGA", "FG_PCT",
            "FG3M", "FG3A", "FG3_PCT",
            "FTM", "FTA", "FT_PCT",
            "OREB", "DREB", "REB",
            "AST", "STL", "BLK", "TOV", "PF",
            "PLUS_MINUS",
        ],
    },
    "fact_player_season_stats": {
        "endpoint": "LeagueDashPlayerStats",
        "grain": "player-season",
        "key_fields": [
            "GP", "GS", "MIN",
            "PTS", "REB", "AST", "STL", "BLK", "TOV",
            "FG_PCT", "FG3_PCT", "FT_PCT",
        ],
    },
    "fact_team_game_boxscore": {
        "endpoint": "BoxScoreTraditionalV2",
        "grain": "team-game",
        "key_fields": [
            "MIN", "PTS", "FGM", "FGA", "FG_PCT",
            "FG3M", "FG3A", "REB", "AST", "STL", "BLK", "TOV", "PF",
        ],
    },
    "v_team_standings": {
        "endpoint": "LeagueStandingsV3",
        "grain": "team-season",
        "key_fields": [
            "W", "L", "WIN_PCT",
            "SEED_RANK", "CONF_RANK", "DIV_RANK",
        ],
    },
    "v_franchise_leaders": {
        "endpoint": "FranchiseLeaders",
        "grain": "franchise",
        "key_fields": [
            "TEAM_ID", "PERSON_ID",
            "PTS", "AST", "REB", "STL", "BLK",
        ],
    },
    "fact_pbp_event": {
        "endpoint": "PlayByPlayV2",
        "grain": "event",
        "key_fields": [
            "EVENTNUM", "PERIOD", "PCTIMESTRING",
            "HOMEDESCRIPTION", "VISITORDESCRIPTION", "NEUTRALDESCRIPTION",
            "SCORE", "SCOREMARGIN",
        ],
    },
    "shots": {
        "endpoint": "ShotChartDetail",
        "grain": "shot",
        "key_fields": [
            "LOC_X", "LOC_Y",
            "SHOT_MADE_FLAG", "SHOT_TYPE", "SHOT_DISTANCE",
            "SHOT_ZONE_BASIC", "SHOT_ZONE_AREA", "SHOT_ZONE_RANGE",
            "SHOT_VALUE",
        ],
    },
}


# ---------------------------------------------------------------------------
# §8.3 Column-case / name normalization.
#
# ``nba_api`` returns UPPERCASE column names (e.g. ``PTS``, ``FG3M``);
# the warehouse uses lowercase snake_case (e.g. ``pts``, ``fg3m``). The
# maps below are the single source of truth for that translation. They
# are deliberately **uppercase -> lowercase** (per the task spec) so
# they read top-to-bottom as "what the official API gives us and how
# the warehouse calls it".
# ---------------------------------------------------------------------------
BOX_SCORE_COLUMN_MAP: dict[str, str] = {
    "MIN": "min",
    "PTS": "pts",
    "FGM": "fgm",
    "FGA": "fga",
    "FG_PCT": "fg_pct",
    "FG3M": "fg3m",
    "FG3A": "fg3a",
    "FG3_PCT": "fg3_pct",
    "FTM": "ftm",
    "FTA": "fta",
    "FT_PCT": "ft_pct",
    "OREB": "oreb",
    "DREB": "dreb",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TOV": "tov",
    "PF": "pf",
    "PLUS_MINUS": "plus_minus",
}

SEASON_TOTALS_COLUMN_MAP: dict[str, str] = {
    "GP": "gp",
    "GS": "gs",
    "MIN": "min",
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TOV": "tov",
    "PF": "pf",
    "FG_PCT": "fg_pct",
    "FG3_PCT": "fg3_pct",
    "FT_PCT": "ft_pct",
}

# Identity-column map. The warehouse uses ``player_id`` /
# ``team_id`` (lowercase) and ``nba_api`` uses ``PERSON_ID`` /
# ``TEAM_ID`` (UPPERCASE). The reverse maps below would not catch
# these because their lowercase forms (``player_id``, ``team_id``)
# never appear as a *value* in BOX_SCORE_COLUMN_MAP or
# SEASON_TOTALS_COLUMN_MAP — only stats. The dedicated identity map
# is consulted by ``_row_get`` so the reconciliation key can be
# written as ``player_id`` on both sides and still match
# ``PERSON_ID`` in the official row.
IDENTITY_COLUMN_MAP: dict[str, str] = {
    "PERSON_ID": "player_id",
    "TEAM_ID": "team_id",
    "GAME_ID": "game_id",
}

# Player-dimension column map. The ``dim_player`` table is keyed by
# ``player_id`` (covered by IDENTITY_COLUMN_MAP above) but also has
# ``full_name``, ``is_active``, ``from_year``, ``to_year`` whose
# ``nba_api`` counterparts come from the CommonAllPlayers /
# CommonPlayerInfo endpoints. Listing them here lets ``reconcile()``
# write the field list in warehouse naming (``full_name``,
# ``is_active``) and still resolve to the official row's
# ``DISPLAY_FIRST_LAST`` / ``ROSTERSTATUS`` / ``FROM_YEAR`` /
# ``TO_YEAR`` keys.
PLAYER_COLUMN_MAP: dict[str, str] = {
    "DISPLAY_FIRST_LAST": "full_name",
    "DISPLAY_LAST_COMMA_FIRST": "full_name",
    "ROSTERSTATUS": "is_active",
    "FROM_YEAR": "from_year",
    "TO_YEAR": "to_year",
    "BIRTHDATE": "birthdate",
    "DRAFT_YEAR": "draft_year",
    "DRAFT_ROUND": "draft_round",
    "DRAFT_NUMBER": "draft_number",
}


# ---------------------------------------------------------------------------
# Pure-function normalization helpers (no I/O, no imports beyond stdlib).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pure-function normalization helpers (no I/O, no imports beyond stdlib).
# ---------------------------------------------------------------------------

def minutes_to_decimal(value: Any) -> float:
    """Convert an ``nba_api`` minutes string to a decimal ``float``.

    ``nba_api`` typically returns ``"MM:SS"`` (e.g. ``"12:30"`` -> 12.5)
    but also surfaces single-minute forms:

    * ``"MM"``      (bare minute count)   e.g. ``"12"``     -> 12.0
    * ``"M:SS"``    (single-digit minute) e.g. ``"9:30"``   -> 9.5
    * ``""`` / ``None`` (DNP rows)                            -> 0.0

    A numeric input is returned unchanged. Anything unparseable raises
    ``ValueError`` — silent coercion would hide DNP/encoding bugs.
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        raise ValueError(f"minutes_to_decimal: bool is not a valid MIN value: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    if ":" in s:
        minutes_part, _, seconds_part = s.partition(":")
        try:
            return float(minutes_part) + float(seconds_part) / 60.0
        except ValueError as e:
            raise ValueError(
                f"minutes_to_decimal: cannot parse MM:SS value {value!r}"
            ) from e
    try:
        return float(s)
    except ValueError as e:
        raise ValueError(
            f"minutes_to_decimal: cannot parse value {value!r}"
        ) from e


def normalize_season(value: Any, *, to: str = "end") -> Any:
    """Bidirectional season normalization (see §8.3 "Season encoding").

    Parameters
    ----------
    value
        A season spec, either the ``"YYYY-YY"`` form (e.g. ``"2023-24"``)
        or an integer/string ending year (e.g. ``2024`` / ``"2024"``).
    to
        ``"end"``        -> return the integer **ending year** (2024)
        ``"hyphenated"`` -> return the ``"YYYY-YY"`` string ("2023-24")

    Examples
    --------
    >>> normalize_season("2023-24")
    2024
    >>> normalize_season(2024, to="hyphenated")
    '2023-24'
    >>> normalize_season("1980", to="hyphenated")
    '1979-80'
    """
    if to not in {"end", "hyphenated"}:
        raise ValueError(
            f"normalize_season: `to` must be 'end' or 'hyphenated', got {to!r}"
        )
    if to == "end":
        s = str(value).strip()
        if "-" in s:
            start = s.split("-", 1)[0]
            return int(start) + 1
        return int(s)
    # to == "hyphenated"
    end_year = int(value)
    start_year = end_year - 1
    return f"{start_year:04d}-{end_year % 100:02d}"


# ---------------------------------------------------------------------------
# §8.3 Reconciliation: row-by-row diff with per-field tolerances.
# ---------------------------------------------------------------------------

def _build_reverse_map(
    source: dict[str, str],
) -> dict[str, list[str]]:
    """Build a one-to-many reverse map (lowercase -> list[uppercase]).

    Some warehouse keys have more than one ``nba_api`` counterpart
    (e.g. ``full_name`` maps to both ``DISPLAY_FIRST_LAST`` and
    ``DISPLAY_LAST_COMMA_FIRST``). The candidates are kept in source
    insertion order so the "canonical" form comes first.
    """
    out: dict[str, list[str]] = {}
    for upper, lower in source.items():
        out.setdefault(lower, []).append(upper)
    return out


# Reverse maps (lowercase warehouse key -> list of uppercase nba_api
# keys, in source-order) used at lookup time inside ``reconcile``.
_REVERSE_BOX_SCORE: dict[str, list[str]] = _build_reverse_map(BOX_SCORE_COLUMN_MAP)
_REVERSE_SEASON_TOTALS: dict[str, list[str]] = _build_reverse_map(SEASON_TOTALS_COLUMN_MAP)
_REVERSE_IDENTITY: dict[str, list[str]] = _build_reverse_map(IDENTITY_COLUMN_MAP)
_REVERSE_PLAYER: dict[str, list[str]] = _build_reverse_map(PLAYER_COLUMN_MAP)
_REVERSE_COLUMN_MAPS: tuple[dict[str, list[str]], ...] = (
    _REVERSE_BOX_SCORE,
    _REVERSE_SEASON_TOTALS,
    _REVERSE_IDENTITY,
    _REVERSE_PLAYER,
)


def _row_get(row: dict[str, Any], field: str) -> Any:
    """Look up ``field`` in ``row``.

    Tries (in order):
    1. the field name as-given (warehouse lowercase),
    2. the field name upper-cased (nba_api UPPERCASE),
    3. the explicit reverse column maps (handles e.g. ``fg_pct`` whose
       uppercase form is identical to the nba_api key, and any future
       rename captured in :data:`BOX_SCORE_COLUMN_MAP`,
       :data:`SEASON_TOTALS_COLUMN_MAP`, :data:`IDENTITY_COLUMN_MAP`,
       :data:`PLAYER_COLUMN_MAP`).

    The reverse-map step is multi-valued: a single warehouse key can
    map to several ``nba_api`` keys (e.g. ``full_name`` maps to both
    ``DISPLAY_FIRST_LAST`` and ``DISPLAY_LAST_COMMA_FIRST``). The
    candidates are tried in source-map insertion order, so the
    "canonical" form wins when both are present.
    """
    if field in row:
        return row[field]
    upper = field.upper()
    if upper in row:
        return row[upper]
    for rev in _REVERSE_COLUMN_MAPS:
        for upper_candidate in rev.get(field, ()):
            if upper_candidate in row:
                return row[upper_candidate]
    return None


def _values_differ(expected: Any, actual: Any, tolerance: float) -> bool:
    """Return True iff the two values differ by more than ``tolerance``.

    Numeric fields are compared with ``abs(float(expected) - float(actual))``
    so integers, ``Decimal``, and floats all work. Non-numeric fields fall
    back to strict ``!=`` equality. ``None`` on either side is a flag
    unless both sides are ``None``.
    """
    if expected is None and actual is None:
        return False
    if expected is None or actual is None:
        return True
    try:
        return abs(float(expected) - float(actual)) > float(tolerance)
    except (TypeError, ValueError):
        return expected != actual


def _severity(expected: Any, actual: Any, tolerance: float) -> str:
    """Bucket a discrepancy by magnitude.

    Heuristic:
      * For zero-tolerance (counting stats): diff ≥ 5 units is "high",
        diff ≥ 2 is "medium", else "low".
      * For non-zero tolerance (percentages / minutes): diff > 5×tolerance
        is "high", diff > 2×tolerance is "medium", else "low".

    Non-numeric mismatches default to "medium".
    """
    try:
        diff = abs(float(expected) - float(actual))
        tol = float(tolerance)
    except (TypeError, ValueError):
        return "medium"
    if tol == 0:
        if diff >= 5:
            return "high"
        if diff >= 2:
            return "medium"
        return "low"
    if diff > 5 * tol:
        return "high"
    if diff > 2 * tol:
        return "medium"
    return "low"


def reconcile(
    fetcher: Callable[..., Iterable[dict[str, Any]]],
    expected_rows: Iterable[dict[str, Any]],
    fields: list[str],
    tolerances: dict[str, float] | None = None,
    key: str | None = None,
) -> list[dict[str, Any]]:
    """Diff official (``fetcher``) rows against warehouse (``expected_rows``) rows.

    Parameters
    ----------
    fetcher
        Zero-arg callable returning the official rows as an iterable of
        dicts. Production wires this to ``get_official_fetcher()``; tests
        pass a ``lambda: [...]`` of planted rows.
    expected_rows
        Iterable of warehouse rows (lowercase keys, the canonical store
        schema).
    fields
        Field names to compare, given in the warehouse (lowercase) naming.
        The first entry is also the row-matching key by default — see
        ``key``.
    tolerances
        Per-field numeric tolerance. Defaults to ``0`` (exact, for
        counting ints). Per §8.3 the canonical example is percentages
        at ``0.001`` (rounding noise).
    key
        Row-matching key field. Defaults to ``fields[0]``.

    Returns
    -------
    list of discrepancy records (dicts), each shaped::

        {"entity": <key value as str>,
         "field":  <field name>,
         "expected": <official value>,
         "actual":   <warehouse value>,
         "severity": "low" | "medium" | "high"}

    A record is produced whenever a key from one side has no counterpart
    on the other (``expected`` or ``actual`` is ``None``) or whenever a
    compared field differs by more than its tolerance.
    """
    if not fields:
        raise ValueError("reconcile: `fields` must be a non-empty list")
    if tolerances is None:
        tolerances = {}
    if key is None:
        key = fields[0]

    official_rows = list(fetcher())
    expected_rows = list(expected_rows)

    official_by_key: dict[Any, dict[str, Any]] = {}
    for r in official_rows:
        k = _row_get(r, key)
        official_by_key[k] = r
    expected_by_key: dict[Any, dict[str, Any]] = {}
    for r in expected_rows:
        k = _row_get(r, key)
        expected_by_key[k] = r

    discrepancies: list[dict[str, Any]] = []
    all_keys: set[Any] = set(official_by_key) | set(expected_by_key)

    for k in all_keys:
        off = official_by_key.get(k)
        exp = expected_by_key.get(k)
        entity = str(k) if k is not None else "<missing>"

        if off is None:
            discrepancies.append({
                "entity": entity,
                "field": key,
                "expected": None,
                "actual": k,
                "severity": "high",
            })
            continue
        if exp is None:
            discrepancies.append({
                "entity": entity,
                "field": key,
                "expected": k,
                "actual": None,
                "severity": "high",
            })
            continue

        for f in fields:
            expected = _row_get(off, f)
            actual = _row_get(exp, f)
            tol = tolerances.get(f, 0)
            if _values_differ(expected, actual, tol):
                discrepancies.append({
                    "entity": entity,
                    "field": f,
                    "expected": expected,
                    "actual": actual,
                    "severity": _severity(expected, actual, tol),
                })

    return discrepancies


# ---------------------------------------------------------------------------
# §8.3 / Issue #9 — Real nba_api-backed fetcher.
#
# The CLI (`python -m basketball_data_emporium.verification.reconcile_official`)
# runs in two modes:
#
#   --run     real HTTP calls to stats.nba.com (the nightly path)
#   --dry-run offline FAKE fetcher (CI plumbing check; no network, no DuckDB)
#
# Of the nine warehouse objects in :data:`ENDPOINT_MAP`, exactly one is
# fully wired end-to-end as of issue #9:
#
#   * dim_player -> nba_api.stats.endpoints.commonallplayers.CommonAllPlayers
#     (one call; bounded by --limit; rows normalized to UPPERCASE dicts)
#
# The other eight raise NotImplementedError with a TODO comment. The
# CLI catches that and reports the endpoint as "skipped (stub)" so the
# nightly does not fail while the remaining endpoints are still being
# wired.
# ---------------------------------------------------------------------------

# Retry / rate-limit policy. These are conservative defaults tuned for
# stats.nba.com's public endpoint (which has been observed to 429 on
# bursts and to time out on first call of the day).
DEFAULT_SLEEP_SECONDS = 0.6
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_TIMEOUT = 30.0

# Default sample cap for nightly runs. A full dim_player row is small,
# so 500 is well under any rate-limit concern; the cap is here to bound
# memory + runtime in CI and to make contract drift between the API
# response and the warehouse visible quickly.
DEFAULT_SAMPLE_LIMIT = 500


class OfficialFetcherError(RuntimeError):
    """Raised by :class:`OfficialFetcher` for unrecoverable nba_api errors."""


class OfficialFetcher:
    """Real nba_api-backed fetcher with rate-limit + retry/backoff.

    Construction triggers the lazy ``nba_api`` import (so the rest of
    the suite can ignore the optional dependency). All per-endpoint
    methods return a list of plain ``dict`` rows with the ``nba_api``
    UPPERCASE column names as keys, suitable for
    :func:`reconcile.reconcile`.

    Policy
    ------
    * **Rate-limit**: ``time.sleep(sleep_seconds)`` is inserted between
      every successful endpoint call (and between retries on the same
      call). The clock used is ``time.monotonic()`` so wall-clock drift
      does not matter.
    * **Retry / backoff**: each per-endpoint invocation is wrapped by
      :meth:`_call_with_retry`. ``max_retries`` retries with
      exponential backoff (``sleep_seconds``, ``sleep_seconds*backoff``,
      ``sleep_seconds*backoff**2``, ...) are performed on transient
      errors — connection errors, timeouts, ``HTTPError`` with
      ``5xx``/``429`` status, or any ``json``/``ValueError`` raised
      while parsing the response. Non-transient errors
      (``KeyError``/``ValueError`` from a clearly malformed response
      shape) surface immediately.
    * **Error surfacing**: when retries are exhausted, the final
      exception is wrapped in :class:`OfficialFetcherError` with a
      message that names the endpoint, the attempt count, and the
      underlying exception class. Callers (the CLI, the nightly
      workflow) can match on the wrapper to decide whether to fail
      the run or to keep going.
    """

    def __init__(
        self,
        *,
        sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        timeout: float = DEFAULT_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if sleep_seconds < 0:
            raise ValueError(
                f"OfficialFetcher: sleep_seconds must be >= 0, got {sleep_seconds}"
            )
        if max_retries < 0:
            raise ValueError(
                f"OfficialFetcher: max_retries must be >= 0, got {max_retries}"
            )
        if backoff_factor < 1.0:
            raise ValueError(
                f"OfficialFetcher: backoff_factor must be >= 1.0, got {backoff_factor}"
            )
        self.sleep_seconds = sleep_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.timeout = timeout
        self._clock = clock
        self._sleeper = sleeper
        self._last_call_at: float = 0.0
        # Bumped on every actual endpoint invocation; useful for
        # instrumenting the nightly report.
        self.call_count = 0

    # ---- internal helpers --------------------------------------------------

    def _rate_limit(self) -> None:
        """Sleep so that consecutive calls are at least ``sleep_seconds`` apart."""
        now = self._clock()
        elapsed = now - self._last_call_at
        if self._last_call_at and elapsed < self.sleep_seconds:
            self._sleeper(self.sleep_seconds - elapsed)
        self._last_call_at = self._clock()

    @staticmethod
    def _is_transient(exc: BaseException) -> bool:
        """Return True for exceptions worth retrying.

        The classification is deliberately conservative — a misclassified
        non-transient error will at worst waste a few extra backoff
        seconds, while a misclassified transient error would surface as
        a false-positive failure. So we lean toward retrying.
        """
        # requests-style network errors
        try:
            import requests  # noqa: F401 — local import keeps this lazy
            from requests.exceptions import (
                ConnectionError as ReqConnectionError,
                Timeout as ReqTimeout,
                HTTPError,
            )

            if isinstance(exc, (ReqConnectionError, ReqTimeout)):
                return True
            if isinstance(exc, HTTPError):
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is None:
                    return True
                return status >= 500 or status == 429
        except ImportError:
            # nba_api depends on requests at runtime, but if it's somehow
            # missing we still want to retry socket-level errors below.
            pass

        # stdlib socket / timeout fallbacks
        import socket

        if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError)):
            return True
        if isinstance(exc, OSError):
            # OSError covers DNS failures, refused connections, etc.
            return True
        # JSON parsing can fail on truncated responses from the API
        # server (e.g. when a 502 returns HTML). Treat that as transient.
        if isinstance(exc, (ValueError, json.JSONDecodeError)):
            return True
        return False

    def _call_with_retry(self, label: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Invoke ``fn`` with rate-limit + exponential backoff retry.

        ``label`` is a human-readable endpoint name (used in error
        messages). The function is invoked up to ``max_retries + 1``
        times; between attempts, ``sleep_seconds`` is multiplied by
        ``backoff_factor ** attempt``.

        Surfacing policy:

        * If ``fn`` raises a **non-transient** error on the first
          attempt (a code bug or a clearly-malformed response), the
          original exception propagates unwrapped. Retrying would
          just waste time and hide the real signal in a generic
          ``OfficialFetcherError``.
        * If ``fn`` raises a **transient** error that survives all
          ``max_retries + 1`` attempts, the final exception is wrapped
          in :class:`OfficialFetcherError` with a message that names
          the endpoint label, the attempt count, and the underlying
          exception class. The original is chained as ``__cause__``.
        * A non-transient error after some successful-but-then-failing
          retries (i.e. a non-transient error surfaces on attempt N
          > 0) is also wrapped — by that point we've already
          committed to a retry cycle, so the caller wants a single
          typed error to handle.
        """
        attempt = 0
        last_exc: BaseException | None = None
        while attempt <= self.max_retries:
            self._rate_limit()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — classify below
                last_exc = exc
                transient = self._is_transient(exc)
                if not transient and attempt == 0:
                    # Code-bug class: don't mask it.
                    raise
                if not transient or attempt == self.max_retries:
                    break
                backoff = self.sleep_seconds * (self.backoff_factor ** attempt)
                logger.warning(
                    "OfficialFetcher: %s attempt %d/%d failed (%s: %s); "
                    "retrying in %.2fs",
                    label, attempt + 1, self.max_retries + 1,
                    type(exc).__name__, exc, backoff,
                )
                # Use the sleeper so tests can inject a no-op.
                self._sleeper(backoff)
                attempt += 1
                continue
            return result
        raise OfficialFetcherError(
            f"{label}: giving up after {self.max_retries + 1} attempt(s); "
            f"last error was {type(last_exc).__name__}: {last_exc}"
        ) from last_exc

    # ---- endpoint implementations ----------------------------------------

    def _fetch_dim_player(self, **params: Any) -> list[dict[str, Any]]:
        """WIRED — CommonAllPlayers.

        The single call is wrapped by :meth:`_call_with_retry`. We
        default ``is_only_current_season`` to ``0`` so the warehouse
        side (which holds historical ``dim_player`` rows) is
        comparable; callers can override via ``params`` (e.g. for a
        ``--limit 5`` smoke test). ``is_only_current_season=1`` would
        return only currently-rostered players.

        The endpoint's documented columns include ``PERSON_ID``,
        ``DISPLAY_FIRST_LAST``, ``ROSTERSTATUS``, ``FROM_YEAR``,
        ``TO_YEAR`` — we surface those and let ``reconcile()`` pick the
        ones it needs (with the UPPERCASE/warehouse key translation
        handled by ``_row_get``).
        """
        from nba_api.stats.endpoints import commonallplayers

        call_params = {
            "is_only_current_season": int(params.get("is_only_current_season", 0)),
            "season": params.get("season", "2025-26"),
            "timeout": int(params.get("timeout", self.timeout)),
        }
        self.call_count += 1
        endpoint_obj = self._call_with_retry(
            "CommonAllPlayers",
            commonallplayers.CommonAllPlayers,
            is_only_current_season=call_params["is_only_current_season"],
            season=call_params["season"],
            timeout=call_params["timeout"],
            get_request=True,
        )
        rows = _data_set_to_rows(endpoint_obj.common_all_players)
        return rows

    def _fetch_dim_game(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire LeagueGameLog. Need season+season_type
        # kwargs, response->row normalization (headers=['GAME_DATE',
        # 'MATCHUP', 'WL', 'MIN', 'PTS', 'SEASON_ID', ...]), and a
        # warehouse-side query (probably nba.api.v_game_summary or
        # dim_game equivalent).
        raise NotImplementedError(
            "dim_game not wired yet; see issue #9 (LeagueGameLog)"
        )

    def _fetch_fact_player_game_boxscore(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire BoxScoreTraditionalV2. Requires a
        # GameID parameter; nightly would need to discover a recent
        # game ID first (e.g. via LeagueGameLog) before calling
        # BoxScoreTraditionalV2. Sample cap probably needs to be a
        # small number of games (e.g. 3) to stay under rate limits.
        raise NotImplementedError(
            "fact_player_game_boxscore not wired yet; see issue #9 (BoxScoreTraditionalV2)"
        )

    def _fetch_fact_player_season_stats(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire LeagueDashPlayerStats. Returns one row
        # per player-season-team with GP, GS, MIN, PTS, REB, AST,
        # STL, BLK, TOV, FG_PCT, FG3_PCT, FT_PCT. Warehouse side is
        # likely nba.api.v_canonical_unified_player_season.
        raise NotImplementedError(
            "fact_player_season_stats not wired yet; see issue #9 (LeagueDashPlayerStats)"
        )

    def _fetch_fact_team_game_boxscore(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire BoxScoreTraditionalV2 (team-side rows,
        # which the same endpoint exposes as a separate result set).
        raise NotImplementedError(
            "fact_team_game_boxscore not wired yet; see issue #9 (BoxScoreTraditionalV2)"
        )

    def _fetch_v_team_standings(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire LeagueStandingsV3. Returns Standings
        # (WINS, LOSSES, WinPCT, DivisionRank, Conference, etc.)
        # for a season snapshot. Warehouse side is
        # nba.api.v_team_standings filtered to the same season.
        raise NotImplementedError(
            "v_team_standings not wired yet; see issue #9 (LeagueStandingsV3)"
        )

    def _fetch_v_franchise_leaders(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire FranchiseLeaders. The endpoint takes
        # TeamID and returns PTS/AST/REB/BLK/STL leaders (with
        # PTS_PERSON_ID, etc.) for that franchise. The warehouse
        # view nba.api.v_franchise_leaders has 5 stat categories
        # per team, so a per-team loop is required.
        raise NotImplementedError(
            "v_franchise_leaders not wired yet; see issue #9 (FranchiseLeaders)"
        )

    def _fetch_fact_pbp_event(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire PlayByPlayV2. Requires a GameID. With
        # ~500 events per game, even one game is large; a per-game
        # keyset + event-number keying is the natural shape.
        raise NotImplementedError(
            "fact_pbp_event not wired yet; see issue #9 (PlayByPlayV2)"
        )

    def _fetch_shots(self, **params: Any) -> list[dict[str, Any]]:
        # TODO(issue-9): wire ShotChartDetail. Requires a PlayerID
        # and a Season/SeasonType. Warehouse side is nba.api.v_shot_chart.
        raise NotImplementedError(
            "shots not wired yet; see issue #9 (ShotChartDetail)"
        )

    # ---- public dispatch --------------------------------------------------

    # Maps warehouse_object -> endpoint method name (string). Adding a
    # new wired endpoint is a one-line change here AND a definition of
    # ``_fetch_<warehouse_object>`` on the class. The dispatch uses
    # ``getattr(self, method_name)`` so subclasses can override
    # individual endpoints.
    _DISPATCH: dict[str, str] = {
        "dim_player": "_fetch_dim_player",
        "dim_game": "_fetch_dim_game",
        "fact_player_game_boxscore": "_fetch_fact_player_game_boxscore",
        "fact_player_season_stats": "_fetch_fact_player_season_stats",
        "fact_team_game_boxscore": "_fetch_fact_team_game_boxscore",
        "v_team_standings": "_fetch_v_team_standings",
        "v_franchise_leaders": "_fetch_v_franchise_leaders",
        "fact_pbp_event": "_fetch_fact_pbp_event",
        "shots": "_fetch_shots",
    }

    def fetch(self, warehouse_object: str, **params: Any) -> list[dict[str, Any]]:
        """Dispatch ``fetch`` to the right endpoint implementation.

        Returns
        -------
        list[dict]
            Rows with UPPERCASE ``nba_api`` keys, ready for
            :func:`reconcile.reconcile`. The list is sliced to
            ``params['limit']`` if provided, so callers (the CLI) can
            bound the row count without changing endpoint code.

        Subclassing
        -----------
        The dispatch uses ``getattr(self, ...)`` so that subclasses
        can override individual endpoint methods (``_fetch_dim_player``
        etc.) and the override is honored. The :data:`_DISPATCH` dict
        is the catalog of known endpoints, not a call binding.
        """
        if warehouse_object not in self._DISPATCH:
            raise ValueError(
                f"Unknown warehouse object: {warehouse_object!r}. "
                f"Known objects: {sorted(self._DISPATCH)}"
            )
        method_name = self._DISPATCH[warehouse_object]
        method = getattr(self, method_name)
        rows = method(**params)
        limit = params.get("limit")
        if limit is not None:
            try:
                limit_int = int(limit)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"OfficialFetcher.fetch: `limit` must be an int, got {limit!r}"
                ) from e
            if limit_int < 0:
                raise ValueError(
                    f"OfficialFetcher.fetch: `limit` must be >= 0, got {limit_int}"
                )
            rows = rows[:limit_int]
        return rows


# ---------------------------------------------------------------------------
# nba_api DataSet -> list[dict] helper.
# ---------------------------------------------------------------------------

def _data_set_to_rows(data_set: Any) -> list[dict[str, Any]]:
    """Convert an ``nba_api`` ``Endpoint.DataSet`` to a list of row-dicts.

    The DataSet exposes a ``data`` dict with ``headers`` (column names)
    and ``data`` (list of row-tuples). Each row is zipped with the
    headers to produce a UPPERCASE-keyed dict suitable for the rest of
    the pipeline.
    """
    headers = list(data_set.data.get("headers") or [])
    raw_rows = data_set.data.get("data") or []
    return [dict(zip(headers, row)) for row in raw_rows]


# ---------------------------------------------------------------------------
# Fake fetcher — for `--dry-run` and offline CI.
#
# This proves the harness plumbing (CLI parsing, report writing, exit
# codes) without touching the network or the warehouse. The planted
# rows are deliberately aligned with what the real endpoint would
# return — UPPERCASE keys, INT-typed IDs, the same column names — so
# the row shape contract is exercised end-to-end.
# ---------------------------------------------------------------------------

def get_fake_fetcher() -> "FakeFetcher":
    """Return a :class:`FakeFetcher` (no network, no warehouse required)."""
    return FakeFetcher()


class FakeFetcher:
    """In-process fetcher with planted rows for offline CI.

    Only the endpoints that have been wired on the real fetcher are
    "available" here; asking for an unwired endpoint raises
    ``NotImplementedError`` to mirror the real behavior.
    """

    # Planted row sets — keyed by warehouse_object. Each row uses the
    # UPPERCASE keys the real nba_api would return.
    _PLANTED: dict[str, list[dict[str, Any]]] = {
        "dim_player": [
            {
                "PERSON_ID": 2544,
                "DISPLAY_FIRST_LAST": "LeBron James",
                "ROSTERSTATUS": 1,
                "FROM_YEAR": "2003",
                "TO_YEAR": "2024",
            },
            {
                "PERSON_ID": 201939,
                "DISPLAY_FIRST_LAST": "Stephen Curry",
                "ROSTERSTATUS": 1,
                "FROM_YEAR": "2009",
                "TO_YEAR": "2024",
            },
            {
                "PERSON_ID": 1628369,
                "DISPLAY_FIRST_LAST": "Jayson Tatum",
                "ROSTERSTATUS": 1,
                "FROM_YEAR": "2017",
                "TO_YEAR": "2024",
            },
        ],
    }

    # Endpoints that are NOT yet wired on the real fetcher — any
    # ``fetch(warehouse_object=...)`` call against one of these will
    # raise NotImplementedError, just like the real fetcher would.
    _STUB: frozenset[str] = frozenset({
        "dim_game",
        "fact_player_game_boxscore",
        "fact_player_season_stats",
        "fact_team_game_boxscore",
        "v_team_standings",
        "v_franchise_leaders",
        "fact_pbp_event",
        "shots",
    })

    def fetch(self, warehouse_object: str, **params: Any) -> list[dict[str, Any]]:
        if warehouse_object in self._STUB:
            raise NotImplementedError(
                f"{warehouse_object} not wired yet; see issue #9"
            )
        if warehouse_object not in self._PLANTED:
            raise ValueError(
                f"FakeFetcher: unknown warehouse object {warehouse_object!r}"
            )
        rows = [dict(r) for r in self._PLANTED[warehouse_object]]
        limit = params.get("limit")
        if limit is not None:
            limit_int = int(limit)
            if limit_int < 0:
                raise ValueError(
                    f"FakeFetcher.fetch: `limit` must be >= 0, got {limit_int}"
                )
            rows = rows[:limit_int]
        return rows


# ---------------------------------------------------------------------------
# Optional ``nba_api``-backed fetcher factory.
#
# We keep the original contract: this raises RuntimeError when
# ``nba_api`` is not installed, otherwise it returns an
# :class:`OfficialFetcher` instance (which has the same dispatch
# shape — ``fetcher.fetch(warehouse_object, **params) -> list[dict]``
# — the old scaffold's closure had).
# ---------------------------------------------------------------------------

def get_official_fetcher() -> OfficialFetcher:
    """Return a real :class:`OfficialFetcher` (uses ``nba_api`` HTTP plumbing).

    Requires the optional ``[reconcile]`` extra::

        pip install basketball-data-emporium[reconcile]

    Raises
    ------
    RuntimeError
        If ``nba_api`` is not importable. The message names the extra so
        the install command is one copy-paste away.
    """
    try:
        from nba_api.stats.endpoints import (  # noqa: F401  (presence check)
            boxscoretraditionalv2,
            commonallplayers,
            commonplayerinfo,
            franchiseleaders,
            leaguedashplayerstats,
            leaguestandingsv3,
            leaguegamelog,
            playbyplayv2,
            shotchartdetail,
        )
    except ImportError as e:
        raise RuntimeError(
            "nba_api is required for cross-source reconciliation but is not "
            "installed. Install the optional extra:\n"
            "  pip install basketball-data-emporium[reconcile]\n"
            "or:  uv add --optional reconcile nba_api>=1.4"
        ) from e

    return OfficialFetcher()


# ---------------------------------------------------------------------------
# §8.3 / Issue #9 — Warehouse-side row extraction.
#
# These functions pull the warehouse rows the official rows will be
# compared against. They are intentionally tiny: a single SQL query,
# a tight key set, no joins. The DUCKDB_PATH env var is honored (see
# ``db.pool._resolve_duckdb_path`` for the same convention).
# ---------------------------------------------------------------------------

def _open_duckdb_read_only() -> Any:
    """Open the warehouse DuckDB file in read-only mode (issue #9 contract).

    The read-only flag is the architectural decision for this whole
    service, and the nightly reconciliation must not violate it. If
    the file is missing we raise :class:`OfficialFetcherError` (the
    same wrapper the fetcher uses) so the CLI can report a clean
    "warehouse unavailable" line.
    """
    import duckdb

    raw = os.environ.get("DUCKDB_PATH", "../data/nba.duckdb")
    path = os.path.abspath(raw)
    if not os.path.exists(path):
        raise OfficialFetcherError(
            f"warehouse DuckDB file not found at {path} "
            f"(DUCKDB_PATH={raw!r}). The nightly job can be run with "
            f"--dry-run if the snapshot is unavailable."
        )
    return duckdb.connect(path, read_only=True)


def fetch_dim_player_from_warehouse(limit: int | None = None) -> list[dict[str, Any]]:
    """Pull ``unified_star.dim_player`` rows for comparison.

    Returns a list of dicts with the warehouse's column names
    (``player_id``, ``full_name``, ``is_active``, ``from_year``,
    ``to_year``). The ``ROSTERSTATUS`` -> ``is_active`` normalization
    is performed on the official side, not here.

    The query is ordered by ``player_id`` so the LIMIT clause is
    deterministic — nightly reports and tests get a stable
    first-N-by-id slice, not whatever the storage engine happens
    to return first.
    """
    limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        "SELECT player_id, full_name, is_active, from_year, to_year "
        "FROM unified_star.dim_player "
        "ORDER BY player_id"
        f"{limit_clause}"
    )
    conn = _open_duckdb_read_only()
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# Mapping: warehouse_object -> (field-name list, tolerance map,
# key, callable to fetch warehouse rows). Only the wired endpoint has
# an entry; the rest are absent so the CLI treats them as stubs.
_WAREHOUSE_FETCH: dict[str, dict[str, Any]] = {
    "dim_player": {
        "fields": [
            "player_id",  # join key
            "full_name",
            "is_active",
            "from_year",
            "to_year",
        ],
        "tolerances": {
            "from_year": 0,
            "to_year": 0,
            # is_active is bool-ish, treat as exact (warehouse stores
            # BOOLEAN; API returns 0/1 which is normalized upstream).
            "is_active": 0,
        },
        "key": "player_id",
        "fetch_warehouse": fetch_dim_player_from_warehouse,
    },
}


def _normalize_official_dim_player(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize CommonAllPlayers UPPERCASE rows to warehouse-friendly shape.

    The ``reconcile()`` row-getter already handles the UPPERCASE/lower
    case mismatch for most fields, but ``ROSTERSTATUS`` is a special
    case: the API returns ``0``/``1`` (sometimes as a string) and the
    warehouse stores a BOOLEAN. Without this normalization,
    ``abs(int(False) - int(True)) == 1`` which trips the
    "counting-int is exact" path. We coerce the API side to bool here
    so the comparison is clean.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        normalized = dict(r)
        roster = normalized.get("ROSTERSTATUS")
        if roster is not None:
            try:
                normalized["ROSTERSTATUS"] = bool(int(roster))
            except (TypeError, ValueError):
                # Leave it alone — the diff will surface it.
                pass
        out.append(normalized)
    return out


# ---------------------------------------------------------------------------
# CLI entry point.
#
# Usage examples (workdir = backend/):
#
#   # Offline plumbing check (no network, no warehouse):
#   ./.venv/Scripts/python.exe -m \
#       basketball_data_emporium.verification.reconcile_official --dry-run
#
#   # Real nightly run, capped at 50 rows per endpoint:
#   ./.venv/Scripts/python.exe -m \
#       basketball_data_emporium.verification.reconcile_official --run --limit 50
#
#   # Custom report path + quiet stdout:
#   ./.venv/Scripts/python.exe -m \
#       basketball_data_emporium.verification.reconcile_official --run --limit 50 \
#       --output reports/reconcile-$(date -I).json
# ---------------------------------------------------------------------------

def _build_cli_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser (kept separate for testability)."""
    parser = argparse.ArgumentParser(
        prog="reconcile_official",
        description=(
            "Cross-source reconciliation: official NBA endpoints "
            "vs. the warehouse DuckDB. See issue #9."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--run",
        action="store_true",
        help="Use the real nba_api-backed fetcher (the nightly path).",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Use the offline FAKE fetcher (no network, no DuckDB). "
            "Useful for CI to exercise the harness."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SAMPLE_LIMIT,
        help=(
            "Row cap per endpoint (default: %(default)s). "
            "Used to keep the nightly bounded."
        ),
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help=(
            "Path to write the JSON discrepancy report "
            "(default: reports/reconcile-<UTC-timestamp>.json)."
        ),
    )
    parser.add_argument(
        "--markdown",
        type=str,
        default=None,
        help=(
            "Optional path to also write a Markdown summary. "
            "If omitted, only the JSON report is produced."
        ),
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        choices=sorted(ENDPOINT_MAP),
        default=None,
        help=(
            "Restrict the run to specific endpoint(s). May be repeated. "
            "If omitted, all endpoints in ENDPOINT_MAP are attempted."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging (per-endpoint timing, row counts).",
    )
    return parser


def _format_json_report(
    *,
    started_at: str,
    finished_at: str,
    mode: str,
    limit: int,
    endpoints: list[dict[str, Any]],
) -> str:
    """Render the per-endpoint results as a JSON string.

    The shape is deliberately flat: a top-level ``meta`` block for the
    nightly runner (timestamps, mode, count of stubs) and an
    ``endpoints`` list with one entry per attempted endpoint. Each
    entry has the warehouse object, the nba_api endpoint name, the
    row counts, the discrepancy count by severity, and either a
    ``discrepancies`` list (when the endpoint ran) or a ``status``
    string (``"stub"`` / ``"error"``).
    """
    payload = {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": finished_at,
        "mode": mode,
        "limit": limit,
        "endpoints": endpoints,
    }
    return json.dumps(payload, indent=2, default=str, sort_keys=False)


def _format_markdown_report(
    *,
    started_at: str,
    finished_at: str,
    mode: str,
    limit: int,
    endpoints: list[dict[str, Any]],
) -> str:
    """Render a compact Markdown summary for the $GITHUB_STEP_SUMMARY."""
    n_total = len(endpoints)
    n_stub = sum(1 for e in endpoints if e.get("status") == "stub")
    n_error = sum(1 for e in endpoints if e.get("status") == "error")
    n_clean = sum(
        1 for e in endpoints
        if e.get("status") == "ok" and not e.get("discrepancy_count", 0)
    )
    n_diff = sum(
        1 for e in endpoints
        if e.get("status") == "ok" and e.get("discrepancy_count", 0)
    )
    high_total = sum(e.get("by_severity", {}).get("high", 0) for e in endpoints)
    med_total = sum(e.get("by_severity", {}).get("medium", 0) for e in endpoints)
    low_total = sum(e.get("by_severity", {}).get("low", 0) for e in endpoints)
    lines = [
        "# Layer 5 reconciliation report",
        "",
        f"- started: `{started_at}`",
        f"- finished: `{finished_at}`",
        f"- mode: `{mode}`",
        f"- per-endpoint limit: `{limit}`",
        "",
        f"- endpoints attempted: **{n_total}**",
        f"  - clean: {n_clean}",
        f"  - with discrepancies: {n_diff}",
        f"  - stubbed (not wired yet): {n_stub}",
        f"  - errored: {n_error}",
        "",
        f"- discrepancy totals: high={high_total}, medium={med_total}, low={low_total}",
        "",
        "## Per-endpoint",
        "",
        "| warehouse_object | endpoint | status | rows | discrepancies |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for e in endpoints:
        status = e.get("status", "?")
        rows = e.get("row_count_official")
        rows_str = "—" if rows is None else str(rows)
        diff = e.get("discrepancy_count", 0)
        lines.append(
            f"| `{e['warehouse_object']}` | {e['endpoint']} | "
            f"{status} | {rows_str} | {diff} |"
        )
    return "\n".join(lines) + "\n"


def _run_reconciliation(
    *,
    args: argparse.Namespace,
    fetcher: Any,
    use_warehouse: bool,
) -> tuple[str, int]:
    """Core reconciliation loop shared by --run and --dry-run.

    Returns
    -------
    (report_json, exit_code)
    """
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    endpoints_to_try = args.endpoint if args.endpoint else sorted(ENDPOINT_MAP)
    endpoint_reports: list[dict[str, Any]] = []

    for wh_obj in endpoints_to_try:
        meta = ENDPOINT_MAP[wh_obj]
        endpoint_label = meta["endpoint"]
        per_endpoint_limit = args.limit
        record: dict[str, Any] = {
            "warehouse_object": wh_obj,
            "endpoint": endpoint_label,
            "status": "ok",
            "row_count_official": None,
            "row_count_warehouse": None,
            "discrepancy_count": 0,
            "by_severity": {"high": 0, "medium": 0, "low": 0},
            "discrepancies": [],
        }
        # 1) Fetch official rows
        official_rows: list[dict[str, Any]]
        try:
            official_rows = fetcher.fetch(wh_obj, limit=per_endpoint_limit)
        except NotImplementedError as e:
            record["status"] = "stub"
            record["skip_reason"] = str(e)
            endpoint_reports.append(record)
            logger.info(
                "endpoint %s: SKIPPED (stub): %s", wh_obj, e
            )
            continue
        except OfficialFetcherError as e:
            record["status"] = "error"
            record["error"] = str(e)
            endpoint_reports.append(record)
            logger.warning("endpoint %s: ERROR: %s", wh_obj, e)
            continue
        except Exception as e:  # noqa: BLE001 — surface in the report
            record["status"] = "error"
            record["error"] = f"{type(e).__name__}: {e}"
            endpoint_reports.append(record)
            logger.warning("endpoint %s: ERROR: %s", wh_obj, e)
            continue

        # 2) Per-endpoint normalization (where needed)
        if wh_obj == "dim_player":
            official_rows = _normalize_official_dim_player(official_rows)
        # The other wired endpoints would normalize here.

        # 3) Fetch warehouse rows + reconcile (only when the endpoint
        #    is fully wired — the absence of an entry in
        #    _WAREHOUSE_FETCH means we have no warehouse query to run).
        if wh_obj not in _WAREHOUSE_FETCH:
            record["row_count_official"] = len(official_rows)
            record["status"] = "no_warehouse_query"
            record["skip_reason"] = (
                "official fetch implemented but no warehouse query registered; "
                "see issue #9"
            )
            endpoint_reports.append(record)
            logger.info(
                "endpoint %s: official-only (%d rows); no warehouse query registered",
                wh_obj, len(official_rows),
            )
            continue

        if not use_warehouse:
            # --dry-run with a wired endpoint: we still report row
            # counts and synthesize an empty discrepancies list so
            # the harness is exercised. No warehouse is queried.
            record["row_count_official"] = len(official_rows)
            record["row_count_warehouse"] = None
            record["discrepancies"] = []
            endpoint_reports.append(record)
            continue

        spec = _WAREHOUSE_FETCH[wh_obj]
        try:
            warehouse_rows = spec["fetch_warehouse"](limit=per_endpoint_limit)
        except OfficialFetcherError as e:
            record["status"] = "error"
            record["error"] = f"warehouse fetch failed: {e}"
            endpoint_reports.append(record)
            logger.warning("endpoint %s: warehouse fetch failed: %s", wh_obj, e)
            continue
        except Exception as e:  # noqa: BLE001
            record["status"] = "error"
            record["error"] = f"warehouse fetch raised {type(e).__name__}: {e}"
            endpoint_reports.append(record)
            logger.warning("endpoint %s: warehouse fetch raised: %s", wh_obj, e)
            continue

        discrepancies = reconcile(
            fetcher=lambda: official_rows,
            expected_rows=warehouse_rows,
            fields=spec["fields"],
            tolerances=spec["tolerances"],
            key=spec["key"],
        )
        record["row_count_official"] = len(official_rows)
        record["row_count_warehouse"] = len(warehouse_rows)
        record["discrepancies"] = discrepancies
        record["discrepancy_count"] = len(discrepancies)
        for d in discrepancies:
            sev = d.get("severity", "low")
            record["by_severity"][sev] = record["by_severity"].get(sev, 0) + 1
        endpoint_reports.append(record)
        logger.info(
            "endpoint %s: %d official / %d warehouse / %d discrepancies",
            wh_obj, len(official_rows), len(warehouse_rows), len(discrepancies),
        )

    finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mode = "run" if args.run else "dry-run"
    report = _format_json_report(
        started_at=started_at,
        finished_at=finished_at,
        mode=mode,
        limit=args.limit,
        endpoints=endpoint_reports,
    )

    # Exit-code policy:
    #   * dry-run is plumbing-only: always exit 0
    #   * run mode with only stubs: exit 0 (nightly does not fail)
    #   * run mode with HIGH/CRITICAL discrepancies: exit 1
    #   * otherwise exit 0
    if args.dry_run:
        exit_code = 0
    else:
        has_high = any(
            e.get("by_severity", {}).get("high", 0) for e in endpoint_reports
        )
        exit_code = 1 if has_high else 0
    return report, exit_code


def _default_output_path() -> str:
    """Return the default report path under ./reports/."""
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return os.path.join("reports", f"reconcile-{ts}.json")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.run:
        # Real fetcher. We let ImportError / RuntimeError surface so the
        # operator sees a clear install hint instead of a silent stub.
        try:
            fetcher = get_official_fetcher()
        except RuntimeError as e:
            print(f"reconcile_official: {e}", file=sys.stderr)
            return 2
        use_warehouse = True
        mode = "run"
    else:
        fetcher = get_fake_fetcher()
        use_warehouse = False
        mode = "dry-run"

    report, exit_code = _run_reconciliation(
        args=args, fetcher=fetcher, use_warehouse=use_warehouse
    )

    out_path = args.output or _default_output_path()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("wrote report to %s", out_path)

    if args.markdown:
        # Re-parse to render markdown (cheap; < 10 KB JSON for 9 endpoints).
        payload = json.loads(report)
        md = _format_markdown_report(
            started_at=payload["started_at"],
            finished_at=payload["finished_at"],
            mode=payload["mode"],
            limit=payload["limit"],
            endpoints=payload["endpoints"],
        )
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info("wrote markdown summary to %s", args.markdown)

    # Console summary line (one line — keep it short for CI logs).
    n_stub = sum(1 for e in json.loads(report)["endpoints"]
                 if e.get("status") == "stub")
    n_err = sum(1 for e in json.loads(report)["endpoints"]
                if e.get("status") == "error")
    n_diff = sum(1 for e in json.loads(report)["endpoints"]
                 if e.get("discrepancy_count", 0))
    high = sum(
        e.get("by_severity", {}).get("high", 0)
        for e in json.loads(report)["endpoints"]
    )
    print(
        f"reconcile_official[{mode}]: report={out_path} "
        f"discrepancies={n_diff} (high={high}) "
        f"stubs={n_stub} errors={n_err} exit={exit_code}"
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
