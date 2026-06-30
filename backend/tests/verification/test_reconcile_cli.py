"""Offline tests for the CLI / `--dry-run` path of the reconciliation harness.

These tests exercise the plumbing added in issue #9 (the OfficialFetcher
class, the FakeFetcher, the CLI main()) without making any network
calls and without touching the DuckDB warehouse. They live next to
``test_reconcile_offline.py`` (which covers the row-diff function)
and are kept separate so the two concerns can evolve independently.

What is covered here:

* ``OfficialFetcher`` construction + the rate-limit / retry helpers
  (using a fake clock/sleeper so no time passes).
* ``OfficialFetcher._is_transient`` classification.
* ``OfficialFetcher._call_with_retry`` retry behavior on transient
  errors and the clean surfacing of the final error.
* ``FakeFetcher`` behavior: returns planted rows for wired endpoints,
  raises ``NotImplementedError`` for the rest.
* The CLI ``--dry-run`` path: parses args, produces a JSON report,
  exits 0, and writes the report to the requested path.
* The exit-code policy: dry-run is always 0, run-with-HIGH-discrepancy
  is 1, run-with-only-stubs is 0.

What is NOT covered here (and intentionally so):

* The real ``nba_api`` HTTP plumbing — that path runs in the nightly
  workflow and is asserted in the workflow itself (artifact upload +
  exit code). A live call in CI would be flaky and rate-limited.
* The actual warehouse DuckDB queries — those go through the same
  read-only path the API serves, so they would need a snapshot.
"""

from __future__ import annotations

import io
import json
import logging
import os

import pytest

from basketball_data_emporium.verification import reconcile_official as ro
from basketball_data_emporium.verification.reconcile_official import (
    DEFAULT_SAMPLE_LIMIT,
    FakeFetcher,
    OfficialFetcher,
    OfficialFetcherError,
    _data_set_to_rows,
    _normalize_official_dim_player,
    get_fake_fetcher,
    get_official_fetcher,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeClock:
    """Monotonic clock that advances only when explicitly bumped."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _RecordingSleeper:
    """``time.sleep``-compatible callable that records every call."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _make_fetcher(**overrides: object) -> OfficialFetcher:
    """Build an OfficialFetcher with an injectable clock + sleeper."""
    clock = overrides.pop("clock", _FakeClock())
    sleeper = overrides.pop("sleeper", _RecordingSleeper())
    return OfficialFetcher(clock=clock, sleeper=sleeper, **overrides)  # type: ignore[arg-type]


def _fake_data_set(headers: list[str], rows: list[tuple]) -> object:
    """Mimic an ``nba_api`` ``Endpoint.DataSet`` for unit tests."""

    class _FakeDataSet:
        def __init__(self) -> None:
            self.data = {"headers": headers, "data": rows}

    return _FakeDataSet()


# ---------------------------------------------------------------------------
# OfficialFetcher — construction
# ---------------------------------------------------------------------------

class TestOfficialFetcherConstruction:
    """Defaults are applied; bad values raise ValueError."""

    def test_defaults_match_module_constants(self) -> None:
        f = OfficialFetcher(clock=lambda: 0.0, sleeper=lambda s: None)
        assert f.sleep_seconds == ro.DEFAULT_SLEEP_SECONDS
        assert f.max_retries == ro.DEFAULT_MAX_RETRIES
        assert f.backoff_factor == ro.DEFAULT_BACKOFF_FACTOR
        assert f.timeout == ro.DEFAULT_TIMEOUT

    def test_negative_sleep_seconds_rejected(self) -> None:
        with pytest.raises(ValueError, match="sleep_seconds"):
            OfficialFetcher(sleep_seconds=-0.1, clock=lambda: 0.0, sleeper=lambda s: None)

    def test_negative_max_retries_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            OfficialFetcher(max_retries=-1, clock=lambda: 0.0, sleeper=lambda s: None)

    def test_backoff_factor_below_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="backoff_factor"):
            OfficialFetcher(backoff_factor=0.5, clock=lambda: 0.0, sleeper=lambda s: None)


# ---------------------------------------------------------------------------
# OfficialFetcher — transient classification
# ---------------------------------------------------------------------------

class TestIsTransient:
    """_is_transient is the heart of the retry policy."""

    def setup_method(self) -> None:
        self.f = _make_fetcher()

    def test_socket_timeout_is_transient(self) -> None:
        import socket
        assert self.f._is_transient(socket.timeout("read")) is True

    def test_connection_error_is_transient(self) -> None:
        assert self.f._is_transient(ConnectionError("refused")) is True

    def test_oserror_is_transient(self) -> None:
        assert self.f._is_transient(OSError("dns failure")) is True

    def test_value_error_is_transient(self) -> None:
        # JSON parse failures on truncated responses should retry.
        assert self.f._is_transient(ValueError("bad json")) is True

    def test_keyerror_is_not_transient(self) -> None:
        # Missing required key in a well-formed response is a code bug.
        assert self.f._is_transient(KeyError("MISSING")) is False

    def test_type_error_is_not_transient(self) -> None:
        assert self.f._is_transient(TypeError("nope")) is False

    def test_5xx_http_error_is_transient(self) -> None:
        # If requests is installed, exercise the HTTPError path.
        try:
            import requests
            from requests.exceptions import HTTPError
        except ImportError:
            pytest.skip("requests not installed; HTTPError path covered in CI log")
        resp = requests.Response()
        resp.status_code = 503
        err = HTTPError(response=resp)
        assert self.f._is_transient(err) is True

    def test_429_http_error_is_transient(self) -> None:
        try:
            import requests
            from requests.exceptions import HTTPError
        except ImportError:
            pytest.skip("requests not installed; HTTPError path covered in CI log")
        resp = requests.Response()
        resp.status_code = 429
        err = HTTPError(response=resp)
        assert self.f._is_transient(err) is True

    def test_4xx_http_error_is_not_transient(self) -> None:
        try:
            import requests
            from requests.exceptions import HTTPError
        except ImportError:
            pytest.skip("requests not installed; HTTPError path covered in CI log")
        resp = requests.Response()
        resp.status_code = 404
        err = HTTPError(response=resp)
        assert self.f._is_transient(err) is False


# ---------------------------------------------------------------------------
# OfficialFetcher — rate-limit + retry
# ---------------------------------------------------------------------------

class TestRateLimitAndRetry:
    """The retry policy must honor backoff and surface final errors."""

    def test_rate_limit_sleeps_to_enforce_floor(self) -> None:
        clock = _FakeClock(t=1000.0)
        sleeper = _RecordingSleeper()
        f = OfficialFetcher(sleep_seconds=2.0, clock=clock, sleeper=sleeper)
        f._rate_limit()  # first call, no sleep
        assert sleeper.sleeps == []
        clock.advance(0.5)
        f._rate_limit()
        # 2.0 - 0.5 = 1.5s of sleep
        assert sleeper.sleeps == [pytest.approx(1.5)]

    def test_retry_recovers_on_transient(self) -> None:
        clock = _FakeClock()
        sleeper = _RecordingSleeper()
        f = OfficialFetcher(
            sleep_seconds=0.1, max_retries=3, backoff_factor=2.0,
            clock=clock, sleeper=sleeper,
        )
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        result = f._call_with_retry("flaky", flaky)
        assert result == "ok"
        assert attempts["n"] == 3
        # Two backoff sleeps between three attempts; both should be > 0
        # and the second should be larger (exponential).
        assert len(sleeper.sleeps) >= 2
        assert sleeper.sleeps[1] > sleeper.sleeps[0]

    def test_retry_gives_up_and_wraps_in_official_fetcher_error(self) -> None:
        clock = _FakeClock()
        sleeper = _RecordingSleeper()
        f = OfficialFetcher(
            sleep_seconds=0.01, max_retries=2, backoff_factor=1.0,
            clock=clock, sleeper=sleeper,
        )

        def always_fails() -> None:
            raise ConnectionError("nope")

        with pytest.raises(OfficialFetcherError) as excinfo:
            f._call_with_retry("always_fails", always_fails)
        msg = str(excinfo.value)
        assert "always_fails" in msg
        assert "ConnectionError" in msg
        assert excinfo.value.__cause__ is not None
        assert isinstance(excinfo.value.__cause__, ConnectionError)

    def test_non_transient_error_does_not_retry(self) -> None:
        clock = _FakeClock()
        sleeper = _RecordingSleeper()
        f = OfficialFetcher(
            sleep_seconds=0.01, max_retries=5, backoff_factor=2.0,
            clock=clock, sleeper=sleeper,
        )
        attempts = {"n": 0}

        def bug() -> None:
            attempts["n"] += 1
            raise KeyError("MISSING")

        with pytest.raises(KeyError):
            f._call_with_retry("bug", bug)
        assert attempts["n"] == 1
        # No backoff sleeps for a non-transient error.
        assert sleeper.sleeps == []


# ---------------------------------------------------------------------------
# OfficialFetcher — fetch dispatch + limit
# ---------------------------------------------------------------------------

class TestFetchDispatch:
    """fetch() routes to the right endpoint and honors the row cap."""

    def test_unknown_warehouse_object_raises(self) -> None:
        f = _make_fetcher()
        with pytest.raises(ValueError, match="Unknown warehouse object"):
            f.fetch("not_a_real_endpoint", limit=1)

    def test_wired_endpoint_returns_rows(self) -> None:
        # We don't make a real call — we subclass OfficialFetcher and
        # override the wired method to return planted rows, then assert
        # that the dispatch + limit plumbing works end-to-end.
        planted = [
            {"PERSON_ID": 1, "DISPLAY_FIRST_LAST": "A"},
            {"PERSON_ID": 2, "DISPLAY_FIRST_LAST": "B"},
            {"PERSON_ID": 3, "DISPLAY_FIRST_LAST": "C"},
        ]

        class _FakeWired(OfficialFetcher):
            def _fetch_dim_player(self, **kwargs):  # type: ignore[override]
                return planted

        f = _FakeWired(clock=lambda: 0.0, sleeper=lambda s: None)
        rows = f.fetch("dim_player", limit=2)
        assert rows == planted[:2]

    def test_stub_endpoint_raises_not_implemented(self) -> None:
        f = _make_fetcher()
        with pytest.raises(NotImplementedError, match="not wired yet"):
            f.fetch("v_team_standings", limit=1)
        with pytest.raises(NotImplementedError, match="not wired yet"):
            f.fetch("fact_pbp_event", limit=1)
        with pytest.raises(NotImplementedError, match="not wired yet"):
            f.fetch("shots", limit=1)

    def test_negative_limit_rejected(self) -> None:
        class _EmptyWired(OfficialFetcher):
            def _fetch_dim_player(self, **kwargs):  # type: ignore[override]
                return []

        f = _EmptyWired(clock=lambda: 0.0, sleeper=lambda s: None)
        with pytest.raises(ValueError, match="`limit`"):
            f.fetch("dim_player", limit=-1)


# ---------------------------------------------------------------------------
# _data_set_to_rows helper
# ---------------------------------------------------------------------------

class TestDataSetToRows:
    def test_zips_headers_and_rows(self) -> None:
        ds = _fake_data_set(
            headers=["PERSON_ID", "NAME"],
            rows=[(1, "A"), (2, "B")],
        )
        assert _data_set_to_rows(ds) == [
            {"PERSON_ID": 1, "NAME": "A"},
            {"PERSON_ID": 2, "NAME": "B"},
        ]

    def test_empty_data_set_returns_empty_list(self) -> None:
        ds = _fake_data_set(headers=[], rows=[])
        assert _data_set_to_rows(ds) == []

    def test_missing_headers_returns_empty_list(self) -> None:
        # A DataSet with no headers (e.g. an empty result set) must
        # not crash — it just produces an empty list.
        class _Ds:
            data: dict = {"headers": None, "data": []}
        assert _data_set_to_rows(_Ds()) == []


# ---------------------------------------------------------------------------
# _normalize_official_dim_player helper
# ---------------------------------------------------------------------------

class TestNormalizeOfficialDimPlayer:
    def test_rosterstatus_int_is_coerced_to_bool(self) -> None:
        rows = [{"PERSON_ID": 1, "ROSTERSTATUS": 1}]
        out = _normalize_official_dim_player(rows)
        assert out[0]["ROSTERSTATUS"] is True

    def test_rosterstatus_zero_is_coerced_to_bool(self) -> None:
        rows = [{"PERSON_ID": 1, "ROSTERSTATUS": 0}]
        out = _normalize_official_dim_player(rows)
        assert out[0]["ROSTERSTATUS"] is False

    def test_rosterstatus_unparseable_is_left_alone(self) -> None:
        rows = [{"PERSON_ID": 1, "ROSTERSTATUS": "weird"}]
        out = _normalize_official_dim_player(rows)
        assert out[0]["ROSTERSTATUS"] == "weird"

    def test_rosterstatus_none_is_left_alone(self) -> None:
        rows = [{"PERSON_ID": 1, "ROSTERSTATUS": None}]
        out = _normalize_official_dim_player(rows)
        assert out[0]["ROSTERSTATUS"] is None


# ---------------------------------------------------------------------------
# FakeFetcher
# ---------------------------------------------------------------------------

class TestFakeFetcher:
    def setup_method(self) -> None:
        self.f = get_fake_fetcher()

    def test_planted_endpoint_returns_rows(self) -> None:
        rows = self.f.fetch("dim_player")
        assert isinstance(rows, list)
        assert len(rows) >= 1
        for r in rows:
            assert "PERSON_ID" in r
            assert "DISPLAY_FIRST_LAST" in r

    def test_limit_honored(self) -> None:
        rows = self.f.fetch("dim_player", limit=1)
        assert len(rows) == 1

    def test_stub_endpoint_raises_not_implemented(self) -> None:
        for wh in (
            "dim_game",
            "fact_player_game_boxscore",
            "fact_player_season_stats",
            "fact_team_game_boxscore",
            "v_team_standings",
            "v_franchise_leaders",
            "fact_pbp_event",
            "shots",
        ):
            with pytest.raises(NotImplementedError, match="not wired yet"):
                self.f.fetch(wh)

    def test_unknown_warehouse_object_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown warehouse object"):
            self.f.fetch("not_a_real_endpoint")

    def test_negative_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match="`limit`"):
            self.f.fetch("dim_player", limit=-1)

    def test_returned_rows_are_independent_copies(self) -> None:
        """Mutating the returned list must not leak into the planted data."""
        rows = self.f.fetch("dim_player")
        rows.clear()
        # Re-fetch should still return the planted rows.
        rows2 = self.f.fetch("dim_player")
        assert len(rows2) >= 1


# ---------------------------------------------------------------------------
# get_official_fetcher — import path
# ---------------------------------------------------------------------------

class TestGetOfficialFetcher:
    """With nba_api installed, returns an OfficialFetcher instance."""

    def test_returns_official_fetcher_instance(self) -> None:
        # nba_api is installed in the test env (via the [reconcile] extra).
        f = get_official_fetcher()
        assert isinstance(f, OfficialFetcher)
        assert hasattr(f, "fetch")
        assert hasattr(f, "call_count")
        assert f.call_count == 0  # no calls have been made yet


# ---------------------------------------------------------------------------
# CLI — --dry-run path
# ---------------------------------------------------------------------------

class TestDryRunCLI:
    """The CLI dry-run must exercise the harness end-to-end without I/O."""

    def test_dry_run_writes_report_and_exits_zero(
        self, tmp_path, capsys, caplog
    ) -> None:
        out_path = tmp_path / "report.json"
        rc = main([
            "--dry-run",
            "--output", str(out_path),
            "--endpoint", "dim_player",
        ])
        assert rc == 0
        assert out_path.exists()
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["mode"] == "dry-run"
        assert payload["limit"] == DEFAULT_SAMPLE_LIMIT
        assert isinstance(payload["endpoints"], list)
        # The chosen endpoint was exercised; nothing else.
        objs = {e["warehouse_object"] for e in payload["endpoints"]}
        assert objs == {"dim_player"}
        # And dim_player was a clean run (no warehouse in dry-run,
        # so discrepancies stay 0).
        dim_player = next(e for e in payload["endpoints"] if e["warehouse_object"] == "dim_player")
        assert dim_player["status"] == "ok"
        assert dim_player["row_count_official"] >= 1
        assert dim_player["discrepancy_count"] == 0
        # Stderr / stdout: a one-line summary is printed.
        captured = capsys.readouterr()
        assert "reconcile_official[dry-run]" in captured.out
        assert f"exit={rc}" in captured.out

    def test_dry_run_with_no_endpoint_runs_all(
        self, tmp_path, caplog
    ) -> None:
        out_path = tmp_path / "report.json"
        rc = main([
            "--dry-run",
            "--output", str(out_path),
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        objs = {e["warehouse_object"] for e in payload["endpoints"]}
        # All nine ENDPOINT_MAP entries attempted.
        assert objs == set(ro.ENDPOINT_MAP)
        # The fake fetcher has planted rows only for dim_player;
        # the other 8 are stubs.
        statuses = {e["warehouse_object"]: e["status"] for e in payload["endpoints"]}
        assert statuses["dim_player"] == "ok"
        for wh, st in statuses.items():
            if wh == "dim_player":
                continue
            assert st == "stub", (wh, st)

    def test_dry_run_default_output_path_is_under_reports(
        self, tmp_path, monkeypatch
    ) -> None:
        # We cd into tmp_path so the default reports/ path is local.
        monkeypatch.chdir(tmp_path)
        rc = main(["--dry-run"])
        assert rc == 0
        # The default output path is reports/reconcile-<UTC>.json
        report_dir = tmp_path / "reports"
        assert report_dir.exists()
        files = list(report_dir.glob("reconcile-*.json"))
        assert len(files) == 1

    def test_dry_run_markdown_also_written(self, tmp_path) -> None:
        out_path = tmp_path / "r.json"
        md_path = tmp_path / "r.md"
        rc = main([
            "--dry-run",
            "--output", str(out_path),
            "--markdown", str(md_path),
        ])
        assert rc == 0
        assert out_path.exists()
        assert md_path.exists()
        md = md_path.read_text(encoding="utf-8")
        # The markdown report must include the Layer 5 header and a
        # table of per-endpoint rows.
        assert "# Layer 5 reconciliation report" in md
        assert "Per-endpoint" in md
        assert "`dim_player`" in md

    def test_dry_run_is_always_zero_regardless_of_stub_count(
        self, tmp_path
    ) -> None:
        # Even with ALL endpoints stubbed (default dry-run), exit 0.
        out_path = tmp_path / "r.json"
        rc = main(["--dry-run", "--output", str(out_path)])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        # 8 stubs + 1 wired = 9.
        n_stub = sum(1 for e in payload["endpoints"] if e["status"] == "stub")
        assert n_stub == 8

    def test_dry_run_limit_clips_row_count(self, tmp_path) -> None:
        out_path = tmp_path / "r.json"
        rc = main([
            "--dry-run",
            "--output", str(out_path),
            "--limit", "1",
            "--endpoint", "dim_player",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["limit"] == 1
        dim_player = next(
            e for e in payload["endpoints"] if e["warehouse_object"] == "dim_player"
        )
        assert dim_player["row_count_official"] == 1

    def test_dry_run_invalid_endpoint_rejected(self) -> None:
        with pytest.raises(SystemExit):
            main(["--dry-run", "--endpoint", "not_a_real_endpoint"])


# ---------------------------------------------------------------------------
# CLI — exit-code policy on a wired endpoint
# ---------------------------------------------------------------------------

class TestExitCodePolicy:
    """The exit-code policy is: HIGH/CRITICAL diff = 1, otherwise 0.

    We assert the policy by patching the OfficialFetcher (via the
    ``--endpoint dim_player`` path) to return rows that disagree with
    the warehouse — this exercises the real reconciliation loop,
    without going to the network.
    """

    def test_run_mode_with_high_discrepancy_exits_nonzero(
        self, tmp_path, monkeypatch, first_dim_player_row
    ) -> None:
        # Force the real fetcher path (--run) by monkeypatching the
        # factory to return an OfficialFetcher whose fetch() returns a
        # planted diff against the warehouse. The warehouse query is
        # ordered by player_id and LIMIT 1'd, so the planted row's
        # player_id must match the warehouse's first row — the
        # ``first_dim_player_row`` fixture (see tests/verification/
        # conftest.py) reads that row and SKIPs the test cleanly when
        # the DuckDB snapshot is absent (hosted CI).
        from basketball_data_emporium.verification import reconcile_official as mod

        player_id, full_name, is_active, from_year, to_year = first_dim_player_row

        class _PlantingFetcher(OfficialFetcher):
            def __init__(self) -> None:
                super().__init__(
                    sleep_seconds=0.0, max_retries=0,
                    clock=lambda: 0.0, sleeper=lambda s: None,
                )
            def fetch(self, warehouse_object, **params):
                # Match the warehouse's key + name + from_year so the
                # only diff is TO_YEAR (planted to a wildly different
                # year so the diff is unambiguously "high").
                return [{
                    "PERSON_ID": player_id,
                    "DISPLAY_FIRST_LAST": full_name,
                    "ROSTERSTATUS": 1 if is_active else 0,
                    "FROM_YEAR": str(from_year),
                    "TO_YEAR": "2099",  # WAY off
                }]

        monkeypatch.setattr(
            mod, "get_official_fetcher", lambda: _PlantingFetcher()
        )
        out_path = tmp_path / "r.json"
        rc = main([
            "--run",
            "--output", str(out_path),
            "--endpoint", "dim_player",
            "--limit", "1",
        ])
        # TO_YEAR diff is large with tolerance 0 -> "high" -> exit 1.
        assert rc == 1
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        dim_player = next(
            e for e in payload["endpoints"] if e["warehouse_object"] == "dim_player"
        )
        assert dim_player["status"] == "ok"
        assert dim_player["by_severity"]["high"] >= 1

    def test_run_mode_clean_dim_player_exits_zero(
        self, tmp_path, monkeypatch, first_dim_player_row
    ) -> None:
        # Mirror the high-discrepancy test, but plant a row that
        # exactly matches the warehouse's first row. Exit code must
        # be 0. Skips cleanly when the DuckDB snapshot is absent.
        from basketball_data_emporium.verification import reconcile_official as mod

        player_id, full_name, is_active, from_year, to_year = first_dim_player_row

        class _CleanFetcher(OfficialFetcher):
            def __init__(self) -> None:
                super().__init__(
                    sleep_seconds=0.0, max_retries=0,
                    clock=lambda: 0.0, sleeper=lambda s: None,
                )
            def fetch(self, warehouse_object, **params):
                return [{
                    "PERSON_ID": player_id,
                    "DISPLAY_FIRST_LAST": full_name,
                    "ROSTERSTATUS": 1 if is_active else 0,
                    "FROM_YEAR": str(from_year),
                    "TO_YEAR": str(to_year),
                }]

        monkeypatch.setattr(
            mod, "get_official_fetcher", lambda: _CleanFetcher()
        )
        out_path = tmp_path / "r.json"
        rc = main([
            "--run",
            "--output", str(out_path),
            "--endpoint", "dim_player",
            "--limit", "1",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        dim_player = next(
            e for e in payload["endpoints"] if e["warehouse_object"] == "dim_player"
        )
        assert dim_player["discrepancy_count"] == 0
        assert dim_player["by_severity"] == {"high": 0, "medium": 0, "low": 0}

    def test_run_mode_only_stub_endpoint_exits_zero(
        self, tmp_path, monkeypatch
    ) -> None:
        from basketball_data_emporium.verification import reconcile_official as mod

        # Patch the factory so we don't need nba_api to be live.
        def _factory() -> OfficialFetcher:
            return OfficialFetcher(
                sleep_seconds=0.0, max_retries=0,
                clock=lambda: 0.0, sleeper=lambda s: None,
            )
        monkeypatch.setattr(mod, "get_official_fetcher", _factory)

        out_path = tmp_path / "r.json"
        rc = main([
            "--run",
            "--output", str(out_path),
            "--endpoint", "v_team_standings",  # a stub
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        standings = next(
            e for e in payload["endpoints"] if e["warehouse_object"] == "v_team_standings"
        )
        assert standings["status"] == "stub"
