const DEFAULT_LOCAL_ORIGIN = "http://localhost:5173";

function parseArgs(argv) {
  const options = {
    localOrigin: DEFAULT_LOCAL_ORIGIN,
    playerId: "2544",
    season: "2024-25",
    seasonType: "Regular Season",
    timeoutMs: 60000,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg === "--local-origin" && next) {
      options.localOrigin = next;
      index += 1;
    } else if (arg === "--player-id" && next) {
      options.playerId = next;
      index += 1;
    } else if (arg === "--season" && next) {
      options.season = next;
      index += 1;
    } else if (arg === "--season-type" && next) {
      options.seasonType = next;
      index += 1;
    } else if (arg === "--timeout-ms" && next) {
      options.timeoutMs = Number(next);
      index += 1;
    }
  }

  return options;
}

function shotDistance(row) {
  return Math.sqrt(row.LOC_X * row.LOC_X + row.LOC_Y * row.LOC_Y) / 10;
}

function classifyShot(row) {
  const distance = shotDistance(row);
  const isThree = row.SHOT_TYPE === "3PT Field Goal";

  let zone;
  if (distance >= 35) zone = "Backcourt";
  else if (isThree && row.LOC_X <= -220 && row.LOC_Y <= 90) zone = "Left Corner 3";
  else if (isThree && row.LOC_X >= 220 && row.LOC_Y <= 90) zone = "Right Corner 3";
  else if (isThree) zone = "Above the Break 3";
  else if (distance <= 4) zone = "Restricted Area";
  else if (distance < 8) zone = "In The Paint (Non-RA)";
  else if (distance < 16 && row.LOC_Y >= 0 && (Math.abs(row.LOC_X) <= 80 || row.LOC_Y >= 80)) {
    zone = "In The Paint (Non-RA)";
  } else {
    zone = "Mid-Range";
  }

  let area;
  if (distance >= 35) area = "Back Court(BC)";
  else if (isThree && row.LOC_X <= -220 && row.LOC_Y <= 90) area = "Left Side(L)";
  else if (isThree && row.LOC_X >= 220 && row.LOC_Y <= 90) area = "Right Side(R)";
  else if (isThree && row.LOC_X < -80) area = "Left Side Center(LC)";
  else if (isThree && row.LOC_X > 80) area = "Right Side Center(RC)";
  else if (isThree) area = "Center(C)";
  else if (distance <= 4) area = "Center(C)";
  else if (distance < 8) area = "Center(C)";
  else if (
    distance < 16 &&
    row.LOC_Y >= 0 &&
    (Math.abs(row.LOC_X) <= 80 || row.LOC_Y >= 80) &&
    row.LOC_X < -80
  ) {
    area = "Left Side(L)";
  } else if (
    distance < 16 &&
    row.LOC_Y >= 0 &&
    (Math.abs(row.LOC_X) <= 80 || row.LOC_Y >= 80) &&
    row.LOC_X > 80
  ) {
    area = "Right Side(R)";
  } else if (distance < 16 && row.LOC_Y >= 0 && (Math.abs(row.LOC_X) <= 80 || row.LOC_Y >= 80)) {
    area = "Center(C)";
  } else if (Math.abs(row.LOC_X) <= 80) area = "Center(C)";
  else if (row.LOC_X < 0 && row.LOC_Y < 80) area = "Left Side(L)";
  else if (row.LOC_X < 0) area = "Left Side Center(LC)";
  else if (row.LOC_X > 0 && row.LOC_Y < 80) area = "Right Side(R)";
  else area = "Right Side Center(RC)";

  let range;
  if (distance >= 35) range = "Back Court Shot";
  else if (isThree) range = "24+ ft.";
  else if (distance < 8) range = "Less Than 8 ft.";
  else if (distance < 16) range = "8-16 ft.";
  else range = "16-24 ft.";

  return { area, range, zone };
}

function keyFor(row) {
  return `${row.zone}||${row.area}||${row.range}`;
}

function localSeasonTypeFor(nbaSeasonType) {
  if (nbaSeasonType === "Regular Season") return "Regular";
  return nbaSeasonType;
}

function aggregate(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = keyFor(row);
    const group = groups.get(key) ?? {
      zone: row.zone,
      area: row.area,
      range: row.range,
      fga: 0,
      fgm: 0,
    };
    group.fga += Number(row.fga);
    group.fgm += Number(row.fgm);
    groups.set(key, group);
  }
  return new Map([...groups.entries()].sort(([a], [b]) => a.localeCompare(b)));
}

function totals(groups) {
  return [...groups.values()].reduce(
    (acc, group) => ({
      fga: acc.fga + group.fga,
      fgm: acc.fgm + group.fgm,
    }),
    { fga: 0, fgm: 0 },
  );
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`);
  }
  return response.json();
}

function nbaStatsUrl(options) {
  const params = new URLSearchParams({
    AheadBehind: "",
    ClutchTime: "",
    ContextFilter: "",
    ContextMeasure: "FGA",
    DateFrom: "",
    DateTo: "",
    EndPeriod: "",
    EndRange: "",
    GameID: "",
    GameSegment: "",
    LastNGames: "0",
    LeagueID: "00",
    Location: "",
    Month: "0",
    OpponentTeamID: "0",
    Outcome: "",
    PORound: "0",
    Period: "0",
    PlayerID: options.playerId,
    PlayerPosition: "",
    PointDiff: "",
    Position: "",
    RangeType: "",
    RookieYear: "",
    Season: options.season,
    SeasonSegment: "",
    SeasonType: options.seasonType,
    StartPeriod: "",
    StartRange: "",
    TeamID: "0",
    VsConference: "",
    VsDivision: "",
  });
  return `https://stats.nba.com/stats/shotchartdetail?${params}`;
}

function rowsFromNbaStats(payload) {
  const shotSet = (payload.resultSets ?? payload.resultSet ?? []).find(
    (set) => set.name === "Shot_Chart_Detail",
  );
  if (!shotSet) throw new Error("NBA Stats response did not include Shot_Chart_Detail.");
  const headers = shotSet.headers;
  return shotSet.rowSet.map((row) =>
    Object.fromEntries(headers.map((header, index) => [header, row[index]])),
  );
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const localUrl = `${options.localOrigin.replace(/\/$/, "")}/api/players/${options.playerId}/shot-splits`;
  const localSeasonType = localSeasonTypeFor(options.seasonType);
  const localRows = await fetchJson(localUrl);
  const localAggregated = aggregate(
    localRows
      .filter((row) => row.season_year === options.season && row.season_type === localSeasonType)
      .map((row) => ({
        zone: row.shot_zone_basic,
        area: row.shot_zone_area,
        range: row.shot_zone_range,
        fga: Number(row.attempts),
        fgm: Number(row.makes),
      })),
  );

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs);
  let nbaRows;
  try {
    const nbaPayload = await fetchJson(nbaStatsUrl(options), {
      signal: controller.signal,
      headers: {
        Accept: "application/json, text/plain, */*",
        Origin: "https://www.nba.com",
        Referer: "https://www.nba.com/",
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
      },
    });
    nbaRows = rowsFromNbaStats(nbaPayload);
  } finally {
    clearTimeout(timeout);
  }

  const nbaAggregated = aggregate(
    nbaRows.map((row) => {
      const classified = classifyShot(row);
      return {
        ...classified,
        fga: 1,
        fgm: Number(row.SHOT_MADE_FLAG),
      };
    }),
  );

  const keys = new Set([...localAggregated.keys(), ...nbaAggregated.keys()]);
  const mismatches = [...keys]
    .sort()
    .map((key) => {
      const local = localAggregated.get(key) ?? { fga: 0, fgm: 0 };
      const nba = nbaAggregated.get(key) ?? { fga: 0, fgm: 0 };
      return {
        key,
        local_fga: local.fga,
        nba_fga: nba.fga,
        local_fgm: local.fgm,
        nba_fgm: nba.fgm,
        fga_delta: local.fga - nba.fga,
        fgm_delta: local.fgm - nba.fgm,
      };
    })
    .filter((row) => row.fga_delta !== 0 || row.fgm_delta !== 0);

  console.log(
    JSON.stringify(
      {
        player_id: options.playerId,
        season: options.season,
        season_type: options.seasonType,
        local_totals: totals(localAggregated),
        nba_totals: totals(nbaAggregated),
        local_zones: localAggregated.size,
        nba_zones: nbaAggregated.size,
        mismatches,
        status: mismatches.length === 0 ? "match" : "mismatch",
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
