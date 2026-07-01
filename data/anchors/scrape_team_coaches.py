#!/usr/bin/env python3
"""Scrape Basketball-Reference franchise index pages and produce a
supplemental coach-by-season table for the warehouse.

``dim_coach`` in the warehouse only has rows for the current (2025-26)
season, so historical coach-by-season lookups (BBR's "Coaches" column on a
team's franchise page, e.g. https://www.basketball-reference.com/teams/BOS/)
have no home in the warehouse today. Rather than re-fetch 30 teams x ~80
seasons of *per-season* roster pages (which is what ``scrape_team_rosters.py``
already caches), this scrapes ONE franchise-index page per team — it already
lists every season's coach(es) and their W-L record for that team in a single
``<table id="teams_franchise">`` (or ``teams_franchise_playoffs``, ignored)
year-by-year table, at a fraction of the request cost.

For each team this script:

  1. Fetches ``https://www.basketball-reference.com/teams/{TLA}/`` (using the
     on-disk HTML cache at ``data/anchors/bbref-pages/team_franchise/`` when
     present).
  2. Parses each season row's ``data-stat="season"`` cell (season end year,
     from the season-page link) and ``data-stat="coaches"`` cell, which may
     list more than one coach per season (mid-season firings), each as
     ``<a href="/coaches/{slug}.html">Initial. Last</a> (W-L)`` with a
     ``csk="Last,First.{end_year}"`` sort-key attribute carrying the full
     first name (the visible text is abbreviated, e.g. "J. Mazzulla").
  3. Maps the team abbreviation to ``team_id`` via ``dim_team`` (same
     lookup as ``scrape_team_rosters.py``).
  4. Emits one row per (team, season, coach) as JSONL.

BBR's published rate-limit guidance is "no more than 20 requests per minute".
Since this only needs one page per team (30 requests total for the full
league), a full run finishes in under two minutes even with the default 3s
delay. Re-runs reuse the on-disk cache and make no new requests.

Usage:
    python data/anchors/scrape_team_coaches.py \\
        --teams ATL,BOS,BRK --out data/anchors/bbr_coaches.jsonl

    # All 30 current franchises:
    python data/anchors/scrape_team_coaches.py --all-teams

    # Reuse existing cache only, no network calls:
    python data/anchors/scrape_team_coaches.py --all-teams --no-network
"""

from __future__ import annotations

import argparse
import gzip
import html as html_lib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "anchors" / "bbref-pages" / "team_franchise"
DEFAULT_OUT = REPO_ROOT / "data" / "anchors" / "bbr_coaches.jsonl"
DEFAULT_DB = REPO_ROOT / "data" / "nba.duckdb"
DEFAULT_DELAY = 3.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; basketball-data-emporium/1.0; "
    "+https://github.com/example/basketball-data-emporium) "
    "BBR-coach-scraper (educational use; respects robots.txt and rate limits)"
)
BBR_BASE = "https://www.basketball-reference.com"

# The 30 current NBA franchises' BBR team-page abbreviations.
ALL_TEAMS = [
    "ATL", "BOS", "BRK", "CHO", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHO", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
]

# Same abbreviation remap as scrape_team_rosters.py — BBR's abbreviation for
# a franchise doesn't always match dim_team.abbreviation.
BBR_TO_DIM_ABBREV: Dict[str, str] = {
    "BRK": "BKN",
    "CHO": "CHA",
    "PHO": "PHX",
    "NOP": "NOP",
    "SAS": "SAN",  # dim_team stores the Spurs as "SAN", not "SAS"
}

# BBR's franchise-index URL always uses the team's *original* abbreviation
# (the page /teams/{current}/ 302-redirects there), so these three current
# codes need to be fetched from their historical slug instead.
FRANCHISE_URL_SLUG: Dict[str, str] = {
    "BRK": "NJN",  # Brooklyn Nets, née New Jersey Nets
    "CHO": "CHA",  # Charlotte Hornets (current stint) — bbref canonical is CHA
    "NOP": "NOH",  # New Orleans Pelicans, née New Orleans Hornets
}

_ROW_RE = re.compile(
    r'<tr[^>]*>(?:(?!<tr).)*?'
    r'data-stat="season"[^>]*>\s*<a href="/teams/[A-Z]+/(?P<end_year>\d{4})\.html">'
    r'.*?'
    r'data-stat="coaches"(?P<td_attrs>[^>]*)>(?P<coaches_cell>.*?)</td>',
    re.DOTALL,
)
_COACH_RE = re.compile(
    r'<a href="/coaches/(?P<slug>[a-z0-9]+)\.html"[^>]*>(?P<label>[^<]+)</a>\s*\((?P<wins>\d+)-(?P<losses>\d+)\)'
)
_CSK_RE = re.compile(r'csk="(?P<csk>[^"]*)"')
_CANONICAL_RE = re.compile(r'<link\s+rel="canonical"\s+href="(?P<url>[^"]+)"', re.IGNORECASE)


def season_label(end_year: int) -> str:
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def parse_coach_cell(cell: str, td_attrs: str) -> List[Dict[str, Any]]:
    coaches: List[Dict[str, Any]] = []
    matches = list(_COACH_RE.finditer(cell))
    # The sortable "csk" attribute ("Last,First.end_year") lives on the <td>
    # itself, not per-<a>, so it only reliably identifies the full name when
    # there was exactly one coach that season (the common case — mid-season
    # coaching changes are rarer and are left with just the abbreviated
    # label BBR shows, e.g. "D. Harris").
    csk_match = _CSK_RE.search(td_attrs)
    first_name = last_name = None
    if csk_match and len(matches) == 1:
        name_part = csk_match.group("csk").rsplit(".", 1)[0]
        if "," in name_part:
            last_name, first_name = (p.strip() for p in name_part.split(",", 1))
    for m in matches:
        coaches.append({
            "bbr_slug": m.group("slug"),
            "coach_label": html_lib.unescape(m.group("label")).strip(),
            "first_name": first_name,
            "last_name": last_name,
            "wins": int(m.group("wins")),
            "losses": int(m.group("losses")),
        })
    return coaches


def parse_franchise_page(html: str) -> List[Dict[str, Any]]:
    """Returns one dict per (season, coach) row found in the year-by-year
    table. Only the first ``data-stat="coaches"`` table on the page is used —
    BBR's franchise page has exactly one (the all-time year-by-year table);
    a defunct-franchise redirect page may have none."""
    rows: List[Dict[str, Any]] = []
    for row_match in _ROW_RE.finditer(html):
        end_year = int(row_match.group("end_year"))
        for coach in parse_coach_cell(row_match.group("coaches_cell"), row_match.group("td_attrs")):
            rows.append({"season_end_year": end_year, **coach})
    return rows


def parse_canonical(html: str) -> Optional[str]:
    m = _CANONICAL_RE.search(html)
    return m.group("url").strip() if m else None


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
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), True
    if not network:
        return "", False
    _sleep_until(last_request_monotonic, delay)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
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


class DbLookups:
    def __init__(self, db_path: Optional[Path]) -> None:
        self._team_abbrev_to_id: Dict[str, int] = {}
        self.loaded = False
        self._load(db_path)

    def _load(self, db_path: Optional[Path]) -> None:
        if db_path is None or not db_path.exists():
            return
        try:
            import duckdb  # type: ignore
        except ImportError:
            print("  duckdb not installed; team_id lookups disabled", file=sys.stderr)
            return
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
        except Exception as e:
            print(f"  could not open DuckDB read-only: {e}", file=sys.stderr)
            return
        try:
            for tid, abbr in conn.execute(
                "SELECT DISTINCT team_id, abbreviation FROM dim_team WHERE abbreviation IS NOT NULL"
            ).fetchall():
                self._team_abbrev_to_id.setdefault(abbr, int(tid))
            self.loaded = True
        except Exception as e:
            print(f"  warning: could not load team abbreviation map: {e}", file=sys.stderr)

    def team_id_for(self, bbr_abbr: str) -> Optional[int]:
        return self._team_abbrev_to_id.get(bbr_abbr)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--teams", default="", help="Comma-separated BBR team abbreviations, e.g. ATL,BOS")
    p.add_argument("--all-teams", action="store_true", help="Scrape all 30 current franchises")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    p.add_argument("--no-network", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.all_teams:
        teams = list(ALL_TEAMS)
    else:
        teams = [t.strip().upper() for t in args.teams.split(",") if t.strip()]
    if not teams:
        print("error: pass --teams or --all-teams", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)
    lookups = DbLookups(db_path if db_path.exists() else None)

    last_req: List[float] = []
    rows_out: List[Dict[str, Any]] = []
    fetched = cached = 0
    skip_no_team = 0

    for idx, tla in enumerate(teams, 1):
        url_slug = FRANCHISE_URL_SLUG.get(tla, tla)
        url = f"{BBR_BASE}/teams/{url_slug}/"
        cache_path = cache_dir / f"{tla}.html"
        html, from_cache = fetch_with_cache(
            url, cache_path,
            user_agent=args.user_agent, delay=args.delay,
            network=not args.no_network, last_request_monotonic=last_req,
        )
        if not html:
            continue
        if from_cache:
            cached += 1
        else:
            fetched += 1
        canonical = parse_canonical(html) or url
        team_id = lookups.team_id_for(BBR_TO_DIM_ABBREV.get(tla, tla))
        if team_id is None:
            skip_no_team += 1
            print(f"  [{idx}/{len(teams)}] {tla}: no team_id match, skipping", file=sys.stderr)
            continue
        season_rows = parse_franchise_page(html)
        for r in season_rows:
            rows_out.append({
                "team_id": team_id,
                "bbr_team": tla,
                "season_year": season_label(r["season_end_year"]),
                "season_end_year": r["season_end_year"],
                "coach_label": r["coach_label"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "wins": r["wins"],
                "losses": r["losses"],
                "bbr_slug": r["bbr_slug"],
                "source_url": canonical,
            })
        print(f"  [{idx}/{len(teams)}] {tla}: {len(season_rows)} coach-season rows (cum {len(rows_out)})", file=sys.stderr)

    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for row in rows_out:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    tmp_path.replace(out_path)

    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta = {
        "generated_on": time.strftime("%Y-%m-%d"),
        "scraper": "data/anchors/scrape_team_coaches.py",
        "teams": teams,
        "fetched": fetched,
        "cached": cached,
        "row_count": len(rows_out),
        "skip_no_team": skip_no_team,
        "lookups_loaded": lookups.loaded,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    print(f"wrote {out_path} — {len(rows_out)} rows ({fetched} fetched, {cached} from cache)")
    print(f"metadata at {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
