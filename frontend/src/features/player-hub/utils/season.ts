/** Season-domain helpers: label formatting and season-end-year parsing. */

export function seasonLabel(seasonEndYear: number): string {
  const start = seasonEndYear - 1;
  return `${start}-${String(seasonEndYear).slice(-2)}`;
}

export function seasonEndYearFromLabel(value: unknown): number | null {
  if (typeof value !== "string") {
    return null;
  }
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) {
    return null;
  }
  const start = Number(match[1]);
  let end = Math.floor(start / 100) * 100 + Number(match[2]);
  if (end <= start) {
    end += 100;
  }
  return end;
}
