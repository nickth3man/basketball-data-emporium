"use client";

import { Activity, Shield, Target } from "lucide-react";
import type { ReactNode } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SectionHeading } from "@/components/section-heading";
import { DataTable } from "@/components/data-table";
import type { PlayerHubSummary } from "@/features/player-hub/types";
import { asNumber, formatStat } from "@/features/player-hub/utils/format";
import { seasonEndYearFromLabel } from "@/features/player-hub/utils/season";

/** Chart gridline color — mirrors the `--color-court-grid` token (SVG stroke can't read CSS vars). */
const GRID_STROKE = "#e4e4e7";

interface OverviewProps {
  summary: PlayerHubSummary;
}

export function Overview({ summary }: OverviewProps) {
  const chartRows = summary.career.rows
    .map((row) => ({
      season: row.season,
      seasonEndYear: seasonEndYearFromLabel(row.season),
      pts: asNumber(row.points_per_game),
      trb: asNumber(row.total_rebounds_per_game),
      ast: asNumber(row.assists_per_game),
    }))
    .filter((row) => row.seasonEndYear !== null)
    .sort((a, b) => (a.seasonEndYear ?? 0) - (b.seasonEndYear ?? 0));

  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-3">
        <Metric icon={<Target className="size-4" />} label="Points" value={formatStat(summary.hero_stats.points_per_game)} />
        <Metric icon={<Shield className="size-4" />} label="Rebounds" value={formatStat(summary.hero_stats.total_rebounds_per_game)} />
        <Metric icon={<Activity className="size-4" />} label="Assists" value={formatStat(summary.hero_stats.assists_per_game)} />
      </div>

      <section className="space-y-3">
        <SectionHeading title="Career Arc" description="Per-game points, rebounds, and assists by season." />
        <div className="h-80 rounded-md border border-court-line bg-white p-3">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartRows} margin={{ top: 8, right: 20, bottom: 8, left: 0 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" />
              <XAxis dataKey="season" tick={{ fontSize: 12 }} minTickGap={24} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="pts" name="PTS" stroke="#0f766e" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="trb" name="TRB" stroke="#b45309" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="ast" name="AST" stroke="#7c3aed" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="space-y-3">
        <SectionHeading title="Career Table" description={`${summary.career.row_count.toLocaleString()} rows from player career stats.`} />
        <DataTable
          rows={summary.career.rows}
          columns={summary.career.columns}
          defaultVisibleColumns={summary.career.default_visible_columns}
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
