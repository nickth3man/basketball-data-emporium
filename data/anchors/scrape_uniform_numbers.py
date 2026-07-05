#!/usr/bin/env python3
"""Scrape Basketball-Reference uniform-number pages and produce the
supplemental jersey-history table for the warehouse.

This is the successor to ``scrape_team_rosters.py`` for jersey data: instead
of one page per (team, season) — ~1,700 pages for full history — BBR's
uniform-number index needs only ~101 pages (numbers 00 and 0-99) to cover
every (player, team, season, number) in NBA/ABA/BAA history:

    https://www.basketball-reference.com/friv/numbers.fcgi?number=45&year=

Each page's table has one row per player: a player link whose href carries
the BBR slug (``/players/j/jordami01.html``) and, per franchise-name era, a
parenthesised list of season links whose hrefs carry the FULL 4-digit season
END year (``numbers.fcgi?number=45&year=1995`` = the 1994-95 season), so no
two-digit century parsing is needed.

ID resolution (DuckDB opened read-only; both lookups fall back gracefully):

  * player: ``bridge_player_bbr`` slug -> ``player_id``, deduped one
    warehouse id per BBR slug by game-log volume (the same rule as
    PLAYER_BBR_XWALK_CTE in web/server/queries/shared.ts), with an
    ASCII-folded name match as fallback for unbridged slugs.
  * team: BBR team display name + season end year -> ``nba_team_id`` via
    ``stg_bref_team_summaries`` (both sides are BBR-sourced, so names match
    exactly, including era names like "New Orleans/Oklahoma City Hornets").

Unlike the roster scraper, rows that fail ID resolution are still written
(with null ``player_id`` / ``team_id``) so ABA/defunct-franchise stints are
preserved; every downstream consumer (web/server/queries/players.ts,
data/audit/build_coach_jersey_tables.sql) already filters nulls itself.

A player who wore two numbers for the same team-season appears on both
number pages (Jordan 1994-95: 45 and 23) and therefore yields two rows;
downstream priority/dedup picks one.

BBR's published rate-limit guidance is "no more than 20 requests per
minute", i.e. >= 3 seconds between requests; the default ``--delay`` is
3.0s. Fetched HTML is cached under ``bbref-pages/uniform_numbers/`` so
re-runs are free and offline-friendly (``--no-network``).

Usage:
    # Full run (~101 pages, ~6 minutes on a cold cache):
    python data/anchors/scrape_uniform_numbers.py

    # Smoke test: one number, cache-only:
    python data/anchors/scrape_uniform_numbers.py \\
        --numbers 45 --out /tmp/smoke.jsonl --no-network
"""

from __future__ import annotations

import argparse
import gzip
import html as html_lib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "anchors" / "bbref-pages" / "uniform_numbers"
DEFAULT_OUT = REPO_ROOT / "data" / "anchors" / "bbr_jerseys.jsonl"
DEFAULT_DB = REPO_ROOT / "data" / "nba.duckdb"
DEFAULT_DELAY = 3.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; basketball-data-emporium/1.0; "
    "+https://github.com/example/basketball-data-emporium) "
    "BBR-uniform-number-scraper (educational use; respects robots.txt and "
    "rate limits)"
)
BBR_BASE = "https://www.basketball-reference.com"

# "00" is a distinct uniform number from "0" (both have been worn), so the
# full sweep is 00 plus 0-99.
ALL_NUMBERS: List[str] = ["00"] + [str(n) for n in range(100)]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
# Active players are rendered as <a ...><strong>Name</strong></a>, hence the
# optional <strong> between the href and the name text.
_PLAYER_RE = re.compile(
    r'<a href="/players/[a-z]/(?P<slug>[a-z0-9]+)\.html">(?:<strong>)?(?P<name>[^<]+)'
)
# One franchise-era segment inside the Team(s) cell: a plain-text team name
# followed by a parenthesised, comma-separated list of season links. The
# team name is whatever text sits between the previous markup and the "(".
_TEAM_SEGMENT_RE = re.compile(
    r">(?P<team>[^<>()]+?)\s*\(\s*(?P<years>(?:<a[^>]*year=\d{4}[^>]*>[^<]*</a>[\s,]*)+)\)"
)
_YEAR_RE = re.compile(r"year=(?P<year>\d{4})")
_CANONICAL_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="(?P<url>[^"]+)"', re.IGNORECASE
)


def season_label(end_year: int) -> str:
    """Convert a season END year (e.g. 1995) to warehouse format "1994-95"."""
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def ascii_fold(name: str) -> str:
    """Strip diacritics and HTML entities so BBR's UTF-8 names (``Varejão``)
    match the warehouse's ASCII names (``Varejao``)."""
    name = html_lib.unescape(name)
    return "".join(
        c for c in unicodedata.normalize("NFKD", name) if unicodedata.category(c) != "Mn"
    )


def parse_numbers_html(page_html: str) -> List[Dict[str, Any]]:
    """Return [{bbr_slug, player_name, team_name, season_end_year}, ...]
    from a uniform-number page. Rows without both a player link and at
    least one season-year link are skipped (headers, nav junk)."""
    rows: List[Dict[str, Any]] = []
    for tr in _TR_RE.finditer(page_html):
        row_html = tr.group(1)
        player = _PLAYER_RE.search(row_html)
        if not player:
            continue
        for seg in _TEAM_SEGMENT_RE.finditer(row_html):
            team_name = html_lib.unescape(seg.group("team")).strip()
            if not team_name:
                continue
            for ym in _YEAR_RE.finditer(seg.group("years")):
                rows.append(
                    {
                        "bbr_slug": player.group("slug").strip(),
                        "player_name": html_lib.unescape(player.group("name")).strip(),
                        "team_name": team_name,
                        "season_end_year": int(ym.group("year")),
                    }
                )
    return rows


def parse_canonical(page_html: str) -> Optional[str]:
    m = _CANONICAL_RE.search(page_html)
    return m.group("url").strip() if m else None


# --------------------------------------------------------------------------
# Network / cache (same pattern as scrape_team_rosters.py)
# --------------------------------------------------------------------------

def _sleep_until(last_request_monotonic: List[float], delay: float) -> None:
    now = time.monotonic()
    if last_request_monotonic and delay > 0:
        elapsed = now - last_request_monotonic[0]
        if elapsed < delay:
            time.sleep(delay - elapsed)
    last_request_monotonic[:] = [time.monotonic()]


def fetch_with_cache(
    url: str,
    cache_path: Path,
    *,
    user_agent: str,
    delay: float,
    network: bool,
    last_request_monotonic: List[float],
) -> Tuple[str, bool]:
    """Return (html, from_cache). With ``network`` False only the cache is
    consulted (missing -> "")."""
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), True
    if not network:
        return "", False
    _sleep_until(last_request_monotonic, delay)
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "text/html,*/*"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            page_html = raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for {url}", file=sys.stderr)
        return "", False
    except urllib.error.URLError as e:
        print(f"  URL error for {url}: {e}", file=sys.stderr)
        return "", False
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(page_html, encoding="utf-8")
    return page_html, False


# --------------------------------------------------------------------------
# DB lookups
# --------------------------------------------------------------------------

class DbLookups:
    """Read-only DuckDB lookups; no-ops when the warehouse is unavailable."""

    def __init__(self, db_path: Optional[Path]) -> None:
        self._slug_to_player: Dict[str, int] = {}
        self._name_to_player: Dict[str, int] = {}
        # (lower team display name, season end year) -> (team_id|None, abbrev|None)
        self._team_season: Dict[Tuple[str, int], Tuple[Optional[int], Optional[str]]] = {}
        # lower team display name -> [(season, team_id, abbrev), ...] for
        # nearest-season fallback (see team_for).
        self._team_name_ids: Dict[str, List[Tuple[int, int, Optional[str]]]] = {}
        self.loaded = False
        if db_path is None or not db_path.exists():
            return
        try:
            import duckdb  # type: ignore
        except ImportError:
            print("  duckdb not installed; lookups disabled", file=sys.stderr)
            return
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
        except Exception as e:
            print(f"  could not open DuckDB read-only: {e}", file=sys.stderr)
            return
        # BBR slug -> player_id, one id per slug ranked by game-log volume —
        # the PLAYER_BBR_XWALK_CTE dedup rule from web/server/queries/shared.ts.
        try:
            for slug, pid in conn.execute(
                """
                SELECT bbr_player_id, player_id AS nba_player_id
                FROM map_player_bbr
                WHERE is_preferred
                """
            ).fetchall():
                self._slug_to_player[str(slug)] = int(pid)
        except Exception as e:
            print(f"  warning: could not load slug crosswalk: {e}", file=sys.stderr)
        # Name fallback for slugs the bridge doesn't know.
        try:
            for pid, name in conn.execute(
                "SELECT DISTINCT player_id, full_name FROM dim_player WHERE full_name IS NOT NULL"
            ).fetchall():
                self._name_to_player.setdefault(str(name), int(pid))
            for pid, name in conn.execute(
                "SELECT DISTINCT TRY_CAST(person_id AS BIGINT), display_first_last "
                "FROM src_common_player_info WHERE display_first_last IS NOT NULL"
            ).fetchall():
                if pid is not None:
                    self._name_to_player.setdefault(str(name), int(pid))
        except Exception as e:
            print(f"  warning: could not load player name map: {e}", file=sys.stderr)
        # BBR team display name + season end year -> nba_team_id + BBR
        # abbreviation. Both sides are BBR-sourced so names match exactly;
        # ABA/defunct rows map to (None, abbrev) and are kept unmapped.
        try:
            for team, season, tid, abbr in conn.execute(
                """
                SELECT DISTINCT team, season, nba_team_id, abbreviation
                FROM src_stg_bref_team_summaries
                WHERE team IS NOT NULL AND season IS NOT NULL
                """
            ).fetchall():
                name_key = str(team).strip().lower()
                key = (name_key, int(season))
                tid_int = int(tid) if tid is not None else None
                abbr_str = str(abbr) if abbr else None
                existing = self._team_season.get(key)
                # Prefer an entry that has an nba_team_id.
                if existing is None or (existing[0] is None and tid_int is not None):
                    self._team_season[key] = (tid_int, abbr_str)
                if tid_int is not None:
                    self._team_name_ids.setdefault(name_key, []).append(
                        (int(season), tid_int, abbr_str)
                    )
        except Exception as e:
            print(f"  warning: could not load team-season map: {e}", file=sys.stderr)
        self.loaded = True

    def player_id_for(self, slug: str, name: str) -> Optional[int]:
        pid = self._slug_to_player.get(slug)
        if pid is not None:
            return pid
        return self._name_to_player.get(ascii_fold(name))

    def team_for(self, team_name: str, end_year: int) -> Tuple[Optional[int], Optional[str]]:
        """Exact (name, season) lookup first; when that row exists but has a
        null nba_team_id (the summaries table lacks ids for whole seasons —
        1971/1976/1977, the same gaps as the legacy ``game`` table), fall
        back to the same name's NEAREST season that has an id. Nearest-season
        keeps the two reused names honest ("Baltimore Bullets" and "Denver
        Nuggets" each denote two different franchises decades apart)."""
        name_key = team_name.strip().lower()
        tid, abbr = self._team_season.get((name_key, end_year), (None, None))
        if tid is not None:
            return tid, abbr
        candidates = self._team_name_ids.get(name_key)
        if not candidates:
            return None, abbr
        season, tid_near, abbr_near = min(candidates, key=lambda c: abs(c[0] - end_year))
        return tid_near, abbr or abbr_near


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--numbers", default=",".join(ALL_NUMBERS),
                   help='Comma-separated uniform numbers (default: 00 plus 0-99). '
                        '"00" and "0" are distinct.')
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help=f"Output JSONL path (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                   help="Directory for cached uniform-number HTML files")
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help=f"Path to DuckDB (default: {DEFAULT_DB.relative_to(REPO_ROOT)})")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"Seconds between live requests (default: {DEFAULT_DELAY})")
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT,
                   help="HTTP User-Agent string")
    p.add_argument("--limit", type=int, default=0,
                   help="If > 0, stop after fetching this many pages live (smoke test)")
    p.add_argument("--no-network", action="store_true",
                   help="Only use the on-disk HTML cache; do not call BBR")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    numbers = [n.strip() for n in args.numbers.split(",") if n.strip()]
    if not numbers:
        print("error: --numbers must be non-empty", file=sys.stderr)
        return 2
    cache_dir = Path(args.cache_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)
    lookups = DbLookups(db_path if db_path.exists() else None)

    last_req: List[float] = []
    rows_out: List[Dict[str, Any]] = []
    skip_counts = {"no_html": 0, "no_rows_parsed": 0}
    unmapped = {"player": 0, "team": 0}
    fetched = cached = 0

    for idx, number in enumerate(numbers, 1):
        url = f"{BBR_BASE}/friv/numbers.fcgi?number={number}&year="
        cache_path = cache_dir / f"number_{number}.html"
        if args.limit and fetched >= args.limit and not cache_path.exists():
            print(f"  --limit {args.limit} reached, stopping", file=sys.stderr)
            break
        page_html, from_cache = fetch_with_cache(
            url,
            cache_path,
            user_agent=args.user_agent,
            delay=args.delay,
            network=not args.no_network,
            last_request_monotonic=last_req,
        )
        if not page_html:
            skip_counts["no_html"] += 1
            continue
        if from_cache:
            cached += 1
        else:
            fetched += 1
        canonical = parse_canonical(page_html) or url
        parsed = parse_numbers_html(page_html)
        if not parsed:
            # Legitimate for numbers nobody has worn; noted, not fatal.
            skip_counts["no_rows_parsed"] += 1
            print(f"  [{idx}/{len(numbers)}] #{number}: no rows", file=sys.stderr)
            continue
        for entry in parsed:
            player_id = lookups.player_id_for(entry["bbr_slug"], entry["player_name"])
            team_id, bbr_abbrev = lookups.team_for(
                entry["team_name"], entry["season_end_year"]
            )
            if player_id is None:
                unmapped["player"] += 1
            if team_id is None:
                unmapped["team"] += 1
            rows_out.append(
                {
                    "player_id": player_id,
                    "team_id": team_id,
                    "season_year": season_label(entry["season_end_year"]),
                    "season_end_year": entry["season_end_year"],
                    "jersey_num": number,
                    "player_name": entry["player_name"],
                    "bbr_slug": entry["bbr_slug"],
                    "bbr_team": bbr_abbrev,
                    "bbr_team_name": entry["team_name"],
                    "source_url": canonical,
                }
            )
        print(
            f"  [{idx}/{len(numbers)}] #{number}: {len(parsed)} rows (cum {len(rows_out)})",
            file=sys.stderr,
        )

    # Atomic replacement, same as scrape_team_rosters.py: consumers reading
    # the old file keep working until the rename lands.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for row in rows_out:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    tmp_path.replace(out_path)

    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta = {
        "generated_on": time.strftime("%Y-%m-%d"),
        "scraper": "data/anchors/scrape_uniform_numbers.py",
        "numbers": numbers,
        "pages_planned": len(numbers),
        "fetched": fetched,
        "cached": cached,
        "row_count": len(rows_out),
        "rows_unmapped_player": unmapped["player"],
        "rows_unmapped_team": unmapped["team"],
        "skip_counts": skip_counts,
        "lookups_loaded": lookups.loaded,
    }
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    print(
        f"wrote {out_path} — {len(rows_out)} rows "
        f"({fetched} fetched, {cached} from cache, "
        f"{unmapped['player']} null player_id, {unmapped['team']} null team_id, "
        f"lookups={'on' if lookups.loaded else 'off'})"
    )
    print(f"metadata at {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
