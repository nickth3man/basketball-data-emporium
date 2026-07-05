/**
 * AnswerChart — an optional inline Recharts viz rendered inside an
 * assistant bubble when the accompanying result table is clearly
 * chartable comparison data.
 *
 * The heuristic in `pickChartSpec` is deliberately conservative: a wrong
 * chart is worse than no chart. We render ONLY when there's a clean label
 * column + 1–3 numeric columns across a small number of rows. Everything
 * is guarded; malformed shapes return `null` and the bubble falls back to
 * the markdown answer + table.
 *
 * Colors come from the `--color-chart-*` tokens (defined in globals.css)
 * and are re-read when the resolved theme changes, so the same chart
 * reads well on light and dark canvases.
 */
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTheme } from "next-themes";

import type { ChatTurnTable } from "@/hooks/useChatTurn";

export interface AnswerChartProps {
  table: ChatTurnTable;
}

interface ChartSpec {
  labelKey: string;
  /** Up to 3 numeric series, each {key, label}. */
  series: { key: string; label: string }[];
  data: Record<string, unknown>[];
  /** Layout: horizontal bars when labels are long or rows are many. */
  layout: "horizontal" | "vertical";
}

const CHART_TOKENS = [
  "--color-chart-1",
  "--color-chart-2",
  "--color-chart-3",
  "--color-chart-4",
  "--color-chart-5",
] as const;

function readChartPalette(): string[] {
  if (typeof window === "undefined") return CHART_TOKENS.map(() => "#e87722");
  const root = getComputedStyle(document.documentElement);
  return CHART_TOKENS.map((token) => root.getPropertyValue(token).trim() || "#e87722");
}

const LABEL_HINTS =
  /^(name|player|team|season|year|pos|position|rank|label|month|date|opp|opponent|conference|division)$/i;

function isNumeric(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * Decide whether `table` is chartable. Returns a ChartSpec or null.
 *
 * Rules:
 *   - 2–16 rows (enough to mean something, few enough to read).
 *   - Exactly one plausible label column: a string-typed column, OR the
 *     first column if its values are mostly strings.
 *   - 1–3 wholly-numeric other columns (the series).
 *   - Numeric column isn't degenerate (all-equal / all-zero).
 */
function pickChartSpec(table: ChatTurnTable): ChartSpec | null {
  const { columns, rows } = table;
  if (rows.length < 2 || rows.length > 16) return null;
  if (columns.length < 2) return null;

  // Classify each column by scanning the rendered rows.
  const classification = columns.map((col) => {
    let nums = 0;
    let strs = 0;
    let nonNull = 0;
    for (const row of rows) {
      const v = row[col.name];
      if (v === null || v === undefined) continue;
      nonNull++;
      if (typeof v === "number" && Number.isFinite(v)) nums++;
      else if (typeof v === "string" && v.trim().length > 0) strs++;
    }
    return {
      name: col.name,
      numeric: nonNull > 0 && nums / nonNull >= 0.9,
      string: nonNull > 0 && strs / nonNull >= 0.7,
    };
  });

  // Pick the label column: prefer an explicit hint, else the first string
  // column, else the first non-numeric column.
  let labelCol = classification.find((c) => LABEL_HINTS.test(c.name) && c.string);
  if (!labelCol) labelCol = classification.find((c) => c.string);
  if (!labelCol) labelCol = classification.find((c) => !c.numeric);
  if (!labelCol) return null;

  const numericCols = classification.filter((c) => c.numeric && c.name !== labelCol.name);
  if (numericCols.length === 0 || numericCols.length > 3) return null;

  // Reject degenerate numeric columns (all the same value).
  const series = numericCols
    .filter((col) => {
      const vals = rows.map((r) => r[col.name]).filter(isNumeric) as number[];
      if (vals.length === 0) return false;
      const min = Math.min(...vals);
      const max = Math.max(...vals);
      return max - min > 1e-9;
    })
    .map((c) => ({ key: c.name, label: c.name }))
    .slice(0, 3);
  if (series.length === 0) return null;

  // Horizontal layout when there are many rows or the labels look long;
  // labels read better on the y-axis with horizontal bars.
  const sampleLabel = String(rows[0]?.[labelCol.name] ?? "");
  const layout: ChartSpec["layout"] =
    rows.length > 8 || sampleLabel.length > 8 ? "horizontal" : "vertical";

  const data = rows.map((r) => {
    const out: Record<string, unknown> = { __label: truncate(String(r[labelCol.name] ?? ""), 22) };
    for (const s of series) out[s.key] = r[s.key];
    return out;
  });

  return { labelKey: "__label", series, data, layout };
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function formatTick(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(2);
}

export function AnswerChart({ table }: AnswerChartProps) {
  const { resolvedTheme } = useTheme();
  const [palette, setPalette] = useState<string[]>(() => readChartPalette());

  // Re-read the palette when the theme flips so chart hues track the canvas.
  useEffect(() => {
    setPalette(readChartPalette());
  }, [resolvedTheme]);

  const spec = pickChartSpec(table);
  if (!spec) return null;

  const isHorizontal = spec.layout === "horizontal";
  const gridStroke = "var(--color-border)";
  const axisStroke = "var(--color-muted-foreground)";
  const tooltipStyle = {
    background: "var(--color-card)",
    border: "1px solid var(--color-border)",
    borderRadius: "0.5rem",
    color: "var(--color-foreground)",
    fontSize: "0.75rem",
    boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
  };

  return (
    <div
      className="mt-1 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-muted)]/40 p-2"
      aria-hidden="true"
    >
      <ResponsiveContainer width="100%" height={isHorizontal ? spec.data.length * 34 + 16 : 200}>
        <BarChart
          data={spec.data}
          layout={spec.layout}
          margin={{ top: 4, right: 8, bottom: 4, left: isHorizontal ? 0 : 4 }}
          barCategoryGap="22%"
        >
          <CartesianGrid
            stroke={gridStroke}
            strokeDasharray="3 3"
            horizontal={!isHorizontal}
            vertical={isHorizontal}
          />
          {isHorizontal ? (
            <>
              <XAxis
                type="number"
                stroke={axisStroke}
                tick={{ fontSize: 10 }}
                tickFormatter={formatTick}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey={spec.labelKey}
                stroke={axisStroke}
                tick={{ fontSize: 10 }}
                width={90}
                axisLine={false}
                tickLine={false}
              />
            </>
          ) : (
            <>
              <XAxis
                type="category"
                dataKey={spec.labelKey}
                stroke={axisStroke}
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                type="number"
                stroke={axisStroke}
                tick={{ fontSize: 10 }}
                tickFormatter={formatTick}
                axisLine={false}
                tickLine={false}
              />
            </>
          )}
          <Tooltip
            cursor={{ fill: "var(--color-primary)", fillOpacity: 0.08 }}
            contentStyle={tooltipStyle}
            labelStyle={{ color: "var(--color-muted-foreground)", fontSize: "0.7rem" }}
          />
          {spec.series.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              fill={palette[i % palette.length]}
              radius={isHorizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]}
              maxBarSize={isHorizontal ? 22 : 44}
              isAnimationActive={false}
            >
              {/* Single-series: color each bar distinctly for a livelier look. */}
              {spec.series.length === 1 &&
                spec.data.map((_, idx) => <Cell key={idx} fill={palette[idx % palette.length]} />)}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
