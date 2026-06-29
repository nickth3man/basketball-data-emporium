#!/usr/bin/env python3
"""Generate data/anchors/manifest.json by walking data/anchors/bbref-pages/.

For each .html file the script records:
  - relative path inside bbref-pages/
  - canonical URL parsed from <link rel="canonical" href="...">
  - MD5 digest of the file
  - byte size
  - <title> text
  - derived entity_type (player_career, team_season, franchise_leaders,
    game_boxscore, season_leaders, advanced_leaders, season_summary, other)
  - derived bbr_slug (player slug, team abbreviation, or game id) where possible
  - derived season_end_year where extractable from the URL

The script is re-runnable: each invocation rewrites manifest.json atomically.
It uses only the Python standard library.

Usage:
    python data/anchors/generate_manifest.py
    python data/anchors/generate_manifest.py --root data/anchors/bbref-pages --out data/anchors/manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Header content for manifest.json
SNAPSHOT_DATE = "2026-06-14"
SUPPLEMENTAL_FETCH_DATE = "2026-06-29"
BBR_BUILD = "req/202605271"  # primary build seen across 422/441 files

# Regex patterns
CANONICAL_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="(?P<url>[^"]+)"\s*/?>',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title>(?P<t>.*?)</title>", re.IGNORECASE | re.DOTALL)
PLAYER_URL_RE = re.compile(
    r"^https?://[^/]+/players/[a-z]/(?P<slug>[a-z0-9]+)\.html$"
)
TEAM_SEASON_URL_RE = re.compile(
    r"^https?://[^/]+/teams/(?P<team>[A-Z]+)/(?P<year>\d{4})\.html$"
)
TEAM_LEADERS_URL_RE = re.compile(
    r"^https?://[^/]+/teams/(?P<team>[A-Z]+)/leaders_career\.html$"
)
BOXSCORE_URL_RE = re.compile(
    r"^https?://[^/]+/boxscores/(?P<game>[A-Za-z0-9]+)\.html$"
)
LEAGUE_LEADERS_URL_RE = re.compile(
    r"^https?://[^/]+/leagues/NBA_(?P<year>\d{4})_leaders\.html$"
)
LEAGUE_ADVANCED_URL_RE = re.compile(
    r"^https?://[^/]+/leagues/NBA_(?P<year>\d{4})_advanced\.html$"
)
LEAGUE_SUMMARY_URL_RE = re.compile(
    r"^https?://[^/]+/leagues/NBA_(?P<year>\d{4})\.html$"
)


def md5_of_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def parse_canonical_and_title(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (canonical_url, title) or (None, None) for stub/404 pages."""
    cm = CANONICAL_RE.search(text)
    canonical = cm.group("url").strip() if cm else None
    tm = TITLE_RE.search(text)
    if tm:
        title = re.sub(r"\s+", " ", tm.group("t")).strip()
    else:
        title = None
    return canonical, title


def classify(canonical: Optional[str]) -> Tuple[str, Optional[str], Optional[int]]:
    """Return (entity_type, bbr_slug, season_end_year)."""
    if not canonical:
        return ("other", None, None)
    for pat, etype in (
        (PLAYER_URL_RE, "player_career"),
        (TEAM_LEADERS_URL_RE, "franchise_leaders"),
        (TEAM_SEASON_URL_RE, "team_season"),
        (BOXSCORE_URL_RE, "game_boxscore"),
        (LEAGUE_LEADERS_URL_RE, "season_leaders"),
        (LEAGUE_ADVANCED_URL_RE, "advanced_leaders"),
        (LEAGUE_SUMMARY_URL_RE, "season_summary"),
    ):
        m = pat.match(canonical)
        if not m:
            continue
        slug = m.groupdict().get("slug") or m.groupdict().get("team") or m.groupdict().get("game")
        year = m.groupdict().get("year")
        return (etype, slug, int(year) if year else None)
    return ("other", None, None)


def walk_html(root: Path) -> Iterable[Path]:
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(".html"):
                yield Path(dirpath) / name


def build_record(path: Path, root: Path) -> Dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    size = path.stat().st_size
    digest = md5_of_file(path)
    # We only need the first ~80 KB to find canonical/title — avoids reading
    # 264 MB of HTML into memory. canonical is on line ~36 in every observed
    # file, and <title> is on line ~31.
    with path.open("rb") as fh:
        head = fh.read(80 * 1024).decode("utf-8", errors="replace")
    canonical, title = parse_canonical_and_title(head)
    entity_type, slug, year = classify(canonical)
    return {
        "path": rel,
        "canonical_url": canonical,
        "md5": digest,
        "size_bytes": size,
        "entity_type": entity_type,
        "bbr_slug": slug,
        "season_end_year": year,
        "title": title,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="data/anchors/bbref-pages",
        help="Anchor corpus root directory (default: data/anchors/bbref-pages).",
    )
    parser.add_argument(
        "--out",
        default="data/anchors/manifest.json",
        help="Output manifest path (default: data/anchors/manifest.json).",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out = Path(args.out).resolve()

    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    files: List[Dict[str, Any]] = []
    md5_counter: Counter[str] = Counter()
    entity_counter: Counter[str] = Counter()

    for path in sorted(walk_html(root)):
        rec = build_record(path, root)
        files.append(rec)
        md5_counter[rec["md5"]] += 1
        entity_counter[rec["entity_type"]] += 1

    unique_by_md5 = sum(1 for _c, n in md5_counter.items() if n == 1)
    duplicates_total = sum(n - 1 for n in md5_counter.values() if n > 1)
    duplicate_groups = sum(1 for n in md5_counter.values() if n > 1)

    manifest: Dict[str, Any] = {
        "snapshot_date": SNAPSHOT_DATE,
        "supplemental_fetch_date": SUPPLEMENTAL_FETCH_DATE,
        "bbr_build": BBR_BUILD,
        "generated_on": date.today().isoformat(),
        "generator": "data/anchors/generate_manifest.py",
        "root": str(root.relative_to(Path.cwd())) if root.is_relative_to(Path.cwd()) else str(root),
        "total_files": len(files),
        "unique_by_md5": unique_by_md5,
        "duplicate_count": duplicates_total,
        "duplicate_groups": duplicate_groups,
        "entity_type_counts": dict(sorted(entity_counter.items())),
        "files": files,
    }

    # Atomic write — temp file then rename
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(out.parent),
        prefix=".manifest.",
        suffix=".json.tmp",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
        json.dump(manifest, tmp, indent=2, ensure_ascii=False, sort_keys=False)
        tmp.write("\n")
    tmp_path.replace(out)

    print(
        f"wrote {out} — {len(files)} files, {unique_by_md5} unique by md5, "
        f"{duplicates_total} duplicates across {duplicate_groups} groups"
    )
    for et, n in sorted(entity_counter.items()):
        print(f"  {et}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
