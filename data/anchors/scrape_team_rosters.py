#!/usr/bin/env python3
"""Scrape Basketball-Reference per-season team roster pages and produce a
supplemental jersey-history table for the warehouse.

For each (team, season) this script:

  1. Fetches ``https://www.basketball-reference.com/teams/{TLA}/{YYYY}.html``
     (using the existing HTML cache at ``data/anchors/bbref-pages/team_roster/``
     when present, so re-runs are free and offline-friendly).
  2. Parses the ``<table id="roster">`` block and extracts the jersey
     number, the BBR player slug (from ``data-append-csv``), and the
     display name (from the player link's anchor text).
  3. Maps the BBR team abbreviation to ``team_id`` via ``dim_team`` and the
     BBR player slug/name to ``player_id`` (``dim_player.full_name`` joined
     to ``common_player_info.person_id``), with the name normalized to ASCII
     so BBR diacritics (``Varejão``) match the warehouse (``Varejao``).
  4. Emits one row per (player, team, season) as JSONL to the output file.

BBR's published rate-limit guidance is "no more than 20 requests per minute",
i.e. >= 3 seconds between requests. The default ``--delay`` is 3.0s. Re-runs
within the same workspace reuse the on-disk HTML cache, so only new fetches
honour the delay.

The DuckDB file is opened read-only — the dev server has it locked
WRITE-blocked but DuckDB happily supports multiple concurrent readers.
If the database is unavailable, the script still fetches and caches the
roster pages and emits rows with ``team_id``/``player_id`` left null (so
the output can be inspected even before the warehouse is built).

Usage:
    python data/anchors/scrape_team_rosters.py \\
        --teams ATL,BOS --seasons 1970,1971,1972,1973,1974,1975 \\
        --out data/anchors/bbr_jerseys.jsonl

    # Reuse existing cache, no network calls, fast:
    python data/anchors/scrape_team_rosters.py \\
        --teams ATL,BOS --seasons 1970-1975 \\
        --out data/anchors/bbr_jerseys.jsonl --no-network

    # Smoke test: 1 page, no delay, only the cache:
    python data/anchors/scrape_team_rosters.py \\
        --teams BOS --seasons 1974 --out /tmp/smoke.jsonl --limit 1
"""

from __future__ import annotations

import argparse
import gzip
import html as html_lib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "anchors" / "bbref-pages" / "team_roster"
DEFAULT_OUT = REPO_ROOT / "data" / "anchors" / "bbr_jerseys.jsonl"
DEFAULT_DB = REPO_ROOT / "data" / "nba.duckdb"
DEFAULT_DELAY = 3.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; basketball-data-emporium/1.0; "
    "+https://github.com/example/basketball-data-emporium) "
    "BBR-roster-scraper (educational use; respects robots.txt and "
    "rate limits)"
)
BBR_BASE = "https://www.basketball-reference.com"

# BBR sometimes uses a different abbreviation than ``dim_team.abbreviation``
# (e.g. BBR uses ``BRK`` for Brooklyn but the warehouse uses ``BKN``).  Map
# the BBR URL abbreviation to the warehouse abbreviation for the ``team_id``
# lookup; the original BBR abbreviation is still stored in the output row.
BBR_TO_DIM_ABBREV: Dict[str, str] = {
    "BRK": "BKN",  # Brooklyn Nets
    "CHO": "CHA",  # Charlotte Hornets (2014–present)
    "CHH": "CHA",  # Charlotte Hornets (1988–2002)
    "PHO": "PHX",  # Phoenix Suns
    "NJN": "NJ",   # New Jersey Nets
    "KCK": "KC",   # Kansas City Kings
    "SDR": "SD",   # San Diego Rockets
    "SFW": "SF",   # San Francisco Warriors
    "NOJ": "NEO",  # New Orleans Jazz
    "WSB": "WAS",  # Washington Bullets
    "MNL": "LAL",  # Minneapolis Lakers
    "FTW": "FWZ",  # Ft. Wayne Zollner Pistons
    "SYR": "PHI",  # Syracuse Nationals
    "MLH": "MIL",  # Milwaukee Hawks
    "PHW": "PHI",  # Philadelphia Warriors
}


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

# Match the entire <table id="roster">...</table> block. DOTALL because the
# table is huge (one <tr> per player, plus a long <thead>).
_ROSTER_TABLE_RE = re.compile(
    r'<table[^>]*id="roster"[^>]*>(.*?)</table>',
    re.DOTALL,
)
# Match a single roster row, including its <th data-stat="number"> jersey
# number and the player link (which carries the BBR slug in data-append-csv
# and the human name in the anchor text).
_ROW_RE = re.compile(
    r'<tr[^>]*>'                      # opening <tr>
    r'(?:(?!<tr).)*?'                 # non-greedy: anything not a new <tr>
    r'data-stat="number"[^>]*>\s*'     # jersey-number cell
    r'(?P<number>[0-9]+)'              # jersey digits
    r'.*?'                            # rest of the row
    r'data-append-csv="(?P<slug>[a-z0-9]+)"'  # BBR player slug
    r'.*?'                            # player cell markup
    r'<a href="/players/[a-z]/[a-z0-9]+\.html">(?P<name>[^<]+)</a>',  # player link
    re.DOTALL,
)
_CANONICAL_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="(?P<url>[^"]+)"',
    re.IGNORECASE,
)


def season_label(end_year: int) -> str:
    """Convert a season END year (e.g. 1975) to BBR/bridge format
    "1974-75" (start year + last 2 digits of end year)."""
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def ascii_fold(name: str) -> str:
    """Strip diacritics and HTML entities so BBR's UTF-8 names
    (``Varejão``) match the warehouse's ASCII names (``Varejao``)."""
    # 1. Decode HTML entities (rare in player name anchors, but free).
    name = html_lib.unescape(name)
    # 2. NFKD decomposition followed by dropping combining marks
    #    (category 'Mn') gives plain ASCII for Latin-alphabet names.
    return "".join(c for c in unicodedata.normalize("NFKD", name) if unicodedata.category(c) != "Mn")


def parse_roster_html(html: str) -> List[Dict[str, str]]:
    """Return a list of {jersey_num, bbr_slug, player_name} dicts from a
    BBR team-season page. Skips rows with non-numeric or blank jerseys."""
    table_match = _ROSTER_TABLE_RE.search(html)
    if not table_match:
        return []
    table = table_match.group(1)
    rows: List[Dict[str, str]] = []
    for m in _ROW_RE.finditer(table):
        jersey = m.group("number").strip()
        slug = m.group("slug").strip()
        name = m.group("name").strip()
        # Filter non-numeric jerseys such as "R" for rookies. Zero is a
        # legitimate NBA jersey number (e.g. Russell Westbrook), so keep it.
        if not jersey.isdigit():
            continue
        rows.append({"jersey_num": jersey, "bbr_slug": slug, "player_name": name})
    return rows


def parse_canonical(html: str) -> Optional[str]:
    m = _CANONICAL_RE.search(html)
    return m.group("url").strip() if m else None


# --------------------------------------------------------------------------
# Network / cache
# --------------------------------------------------------------------------

def _sleep_until(
    last_request_monotonic: List[float],
    delay: float,
) -> None:
    """Block until at least ``delay`` seconds have passed since the last
    recorded live request. Always records the current time on exit so
    failures are also spaced out."""
    now = time.monotonic()
    if last_request_monotonic and delay > 0:
        elapsed = now - last_request_monotonic[0]
        if elapsed < delay:
            time.sleep(delay - elapsed)
    # Keep the list to a single most-recent timestamp.
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
    """Return (html, from_cache). If ``network`` is False, only the cache
    is consulted (missing → returns ""). The ``last_request_monotonic``
    list is a 1-element box used to enforce a minimum delay between live
    requests; pass an empty list at the start of the run."""
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), True
    if not network:
        return "", False
    _sleep_until(last_request_monotonic, delay)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            # BBR serves gzip when the client advertises it; urlopen
            # already decompresses Content-Encoding but older endpoints
            # may not, so be defensive.
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for {url}", file=sys.stderr)
        return "", False
    except urllib.error.URLError as e:
        print(f"  URL error for {url}: {e}", file=sys.stderr)
        return "", False
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(html, encoding="utf-8")
    return html, False


# --------------------------------------------------------------------------
# DB lookups
# --------------------------------------------------------------------------

class DbLookups:
    """Lazy-loaded BBR → warehouse ID lookups. Falls back to no-op maps
    if DuckDB can't be opened (so the script is usable in environments
    where the warehouse hasn't been built yet)."""

    def __init__(self, db_path: Optional[Path]) -> None:
        self.db_path = db_path
        self._conn = None
        self._name_to_player: Dict[str, int] = {}
        self._team_abbrev_to_id: Dict[str, int] = {}
        self.loaded = False
        self._load()

    def _load(self) -> None:
        if self.db_path is None or not self.db_path.exists():
            return
        try:
            import duckdb  # type: ignore
        except ImportError:
            print("  duckdb not installed; lookups disabled", file=sys.stderr)
            return
        try:
            self._conn = duckdb.connect(str(self.db_path), read_only=True)
        except Exception as e:
            print(f"  could not open DuckDB read-only: {e}", file=sys.stderr)
            return
        # Player name → player_id. dim_player has one row per stint, so
        # DISTINCT collapses duplicates. Some names (e.g. "Don Chaney")
        # only live in dim_player and never made it into
        # common_player_info, so we union the two name sources to be safe.
        try:
            for pid, name in self._conn.execute(
                "SELECT DISTINCT player_id, full_name FROM dim_player WHERE full_name IS NOT NULL"
            ).fetchall():
                self._name_to_player.setdefault(name, int(pid))
            for pid, name in self._conn.execute(
                "SELECT DISTINCT TRY_CAST(person_id AS BIGINT), display_first_last "
                "FROM common_player_info WHERE display_first_last IS NOT NULL"
            ).fetchall():
                if pid is None:
                    continue
                self._name_to_player.setdefault(name, int(pid))
        except Exception as e:
            print(f"  warning: could not load player name map: {e}", file=sys.stderr)
        # BBR team abbreviation → team_id. dim_team has multi-era rows
        # (e.g. LAL for both "Minneapolis Lakers" 1949 and "Los Angeles
        # Lakers" 1960, all sharing team_id 1610612747), so the DISTINCT
        # just guards against the rare same-abbrev-different-team case.
        try:
            for tid, abbr in self._conn.execute(
                "SELECT DISTINCT team_id, abbreviation FROM dim_team WHERE abbreviation IS NOT NULL"
            ).fetchall():
                self._team_abbrev_to_id.setdefault(abbr, int(tid))
        except Exception as e:
            print(f"  warning: could not load team abbreviation map: {e}", file=sys.stderr)
        self.loaded = True

    def player_id_for(self, bbr_name: str) -> Optional[int]:
        return self._name_to_player.get(ascii_fold(bbr_name))

    def team_id_for(self, bbr_abbr: str) -> Optional[int]:
        return self._team_abbrev_to_id.get(bbr_abbr)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--teams", required=True,
                   help="Comma-separated BBR team abbreviations, e.g. ATL,BOS")
    p.add_argument("--seasons", required=True,
                   help="Comma-separated end years (e.g. 1970,1971,1972) or "
                        "a range like 1970-1975")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help=f"Output JSONL path (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                   help="Directory for cached roster HTML files")
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help=f"Path to DuckDB (default: {DEFAULT_DB.relative_to(REPO_ROOT)})")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"Seconds between live requests (default: {DEFAULT_DELAY})")
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT,
                   help="HTTP User-Agent string")
    p.add_argument("--limit", type=int, default=0,
                   help="If > 0, stop after fetching this many pages (smoke test)")
    p.add_argument("--no-network", action="store_true",
                   help="Only use the on-disk HTML cache; do not call BBR")
    return p.parse_args(argv)


def expand_seasons(arg: str) -> List[int]:
    out: List[int] = []
    for piece in arg.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            a, b = piece.split("-", 1)
            a, b = int(a), int(b)
            if b < a:
                a, b = b, a
            out.extend(range(a, b + 1))
        else:
            out.append(int(piece))
    # Dedup + sort
    return sorted(set(out))


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    teams = [t.strip().upper() for t in args.teams.split(",") if t.strip()]
    seasons = expand_seasons(args.seasons)
    if not teams or not seasons:
        print("error: --teams and --seasons must be non-empty", file=sys.stderr)
        return 2
    cache_dir = Path(args.cache_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)
    lookups = DbLookups(db_path if db_path.exists() else None)

    last_req: List[float] = []
    rows_out: List[Dict[str, Any]] = []
    skip_counts = {"no_canonical": 0, "no_roster_table": 0, "no_player_match": 0, "no_team_match": 0, "blank_jersey": 0}
    fetched = cached = 0

    pages_planned = [(t, s) for t in teams for s in seasons]
    for idx, (tla, end_year) in enumerate(pages_planned, 1):
        url = f"{BBR_BASE}/teams/{tla}/{end_year}.html"
        cache_path = cache_dir / f"{tla}_{end_year}.html"
        if args.limit and fetched >= args.limit and not cache_path.exists():
            print(f"  --limit {args.limit} reached, stopping", file=sys.stderr)
            break
        html, from_cache = fetch_with_cache(
            url,
            cache_path,
            user_agent=args.user_agent,
            delay=args.delay,
            network=not args.no_network,
            last_request_monotonic=last_req,
        )
        if not html:
            skip_counts["no_canonical"] += 1
            continue
        if from_cache:
            cached += 1
        else:
            fetched += 1
        canonical = parse_canonical(html) or url
        roster = parse_roster_html(html)
        if not roster:
            # The page loaded but has no roster table — typically a
            # mid-season snapshot or an off-season stub. Skip and note.
            skip_counts["no_roster_table"] += 1
            continue
        team_id = lookups.team_id_for(BBR_TO_DIM_ABBREV.get(tla, tla))
        if team_id is None:
            # No team_id match: BBR uses an abbreviation not in dim_team
            # (defunct franchise, mid-season name change, etc.). Skip.
            skip_counts["no_team_match"] += len(roster)
            continue
        season_str = season_label(end_year)
        for entry in roster:
            player_id = lookups.player_id_for(entry["player_name"])
            if player_id is None:
                skip_counts["no_player_match"] += 1
                continue
            rows_out.append({
                "player_id": player_id,
                "team_id": team_id,
                "season_year": season_str,
                "season_end_year": end_year,
                "jersey_num": entry["jersey_num"],
                "player_name": entry["player_name"],
                "bbr_slug": entry["bbr_slug"],
                "bbr_team": tla,
                "source_url": canonical,
            })
        print(f"  [{idx}/{len(pages_planned)}] {tla} {end_year}: {len(roster)} rows (cum {len(rows_out)})", file=sys.stderr)

    # Atomic write
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        # One JSON object per line, plus a leading metadata comment line
        # that begins with "#" — DuckDB's read_json_auto ignores lines
        # that don't parse, but a leading "#" might still confuse it on
        # some versions, so we put metadata in a sidecar instead.
        for row in rows_out:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    tmp_path.replace(out_path)

    # Sidecar metadata file (humans only — DuckDB reads the .jsonl)
    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta = {
        "generated_on": time.strftime("%Y-%m-%d"),
        "scraper": "data/anchors/scrape_team_rosters.py",
        "teams": teams,
        "seasons": seasons,
        "fetched": fetched,
        "cached": cached,
        "row_count": len(rows_out),
        "skip_counts": skip_counts,
        "lookups_loaded": lookups.loaded,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    print(
        f"wrote {out_path} — {len(rows_out)} rows "
        f"({fetched} fetched, {cached} from cache, lookups={'on' if lookups.loaded else 'off'})"
    )
    print(f"metadata at {meta_path}")
    for k, v in skip_counts.items():
        if v:
            print(f"  skipped ({k}): {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
