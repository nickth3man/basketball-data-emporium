"use client";

import { Activity, Shield, Target } from "lucide-react";
import type { ReactNode } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { EmptyState } from "@/components/empty-state";
import { SectionHeading } from "@/components/section-heading";
import { DataTable } from "@/components/data-table";
import type { TeamHeroStats, TeamHubSummary } from "@/features/team-hub/types";
import { asNumber, formatStat } from "@/lib/format";

/** Chart gridline color — mirrors the `--color-court-grid` token (SVG stroke can't read CSS vars). */
const GRID_STROKE = "#e4e4e7";

interface OverviewProps {
  summary: TeamHubSummary;
}

export function Overview({ summary }: OverviewProps) {
  const hero = summary.hero_stats;
  const chartRows = (summary.franchise_arc ?? [])
    .slice()
    .sort((a, b) => a.season_end_year - b.season_end_year);

  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-3">
        <Metric icon={<Target className="size-4" />} label="Wins" value={formatStat(asNumber(hero.wins))} />
        <Metric icon={<Shield className="size-4" />} label="Losses" value={formatStat(asNumber(hero.losses))} />
        <Metric
          icon={<Activity className="size-4" />}
          label="Win %"
          value={formatStat(formatWinPct(hero.win_pct), "%")}
        />
      </div>

      <section className="space-y-3">
        <SectionHeading
          title="Franchise Arc"
          description="Season-by-season trends for this franchise."
        />
        {chartRows.length === 0 ? (
          <EmptyState
            title="No franchise arc rows"
            detail="This team has no season-history rows in the current DuckDB snapshot."
          />
        ) : (
          <div className="h-80 rounded-md border border-court-line bg-white p-3">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartRows} margin={{ top: 8, right: 20, bottom: 8, left: 0 }}>
                <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" />
                <XAxis
                  dataKey="season_end_year"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tick={{ fontSize: 12 }}
                  minTickGap={24}
                />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="wins"
                  name="Wins"
                  stroke="#0f766e"
                  strokeWidth={2}
                  dot
                  connectNulls
                />
                <Line
                  type="monotone"
                  dataKey="losses"
                  name="Losses"
                  stroke="#b45309"
                  strokeWidth={2}
                  dot
                  connectNulls
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Roster"
          description={`${summary.roster.row_count.toLocaleString()} rows from the current roster.`}
        />
        <DataTable
          rows={summary.roster.rows}
          columns={summary.roster.columns}
          defaultVisibleColumns={summary.roster.default_visible_columns}
        />
      </section>
    </div>
  );
}

function Metric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-md border border-court-line bg-white p-4">
      <div className="flex items-center gap-2 text-sm text-court-muted">
        <span className="text-court-accent" aria-hidden="true">
          {icon}
        </span>
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums text-court-ink">{value}</div>
    </div>
  );
}

/**
 * Format `win_pct` for the hero stat card. The server may emit the value
 * as `0.612` (a ratio) or as `"61.2"` (a percent). The em-dash fallback
 * is provided by `formatStat` upstream — we only need to coerce a ratio
 * to a percentage so the cell reads "61.2%" instead of "0.6%".
 */
function formatWinPct(value: number | null | undefined): number | null {
  const parsed = asNumber(value);
  if (parsed === null) {
    return null;
  }
  // Ratios in [0, 1] (most common server shape) get scaled to percent.
  // A value already > 1 is assumed to be a pre-scaled percent and is
  // returned unchanged.
  return parsed > 1 ? parsed : parsed * 100;
}

export type { TeamHeroStats };
