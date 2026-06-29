/**
 * Shared formatting helpers — pure functions, feature-agnostic.
 *
 * Mirrored from `ui/src/features/player-hub/utils/format.ts` (the original
 * home). The player-hub utils module now re-exports these symbols for
 * backward compatibility with any consumer that has not been migrated to
 * the shared `@/lib/format` path yet.
 */

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(", ") : "—";
  }
  if (typeof value === "number") {
    if (Number.isInteger(value)) {
      return value.toLocaleString();
    }
    return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  return String(value);
}

export function formatStat(value: unknown, suffix = ""): string {
  const formatted = formatValue(value);
  return formatted === "—" ? formatted : `${formatted}${suffix}`;
}

export function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
