/**
 * Tiny CSV serializer (PLAN §8.3 — ResultTable's "Copy CSV" affordance).
 *
 * - Quotes any cell containing a comma, double-quote, CR, or LF.
 * - Escapes embedded double-quotes by doubling them (RFC 4180).
 * - Uses CRLF line endings (Excel-friendly; RFC 4180 default).
 *
 * No external dep — the typical user copy is <10k rows so the O(N)
 * string concat is fine; V8 handles this quickly.
 */
export function rowsToCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const escapeCell = (raw: unknown): string => {
    if (raw === null || raw === undefined) return "";
    const str = typeof raw === "string" ? raw : String(raw);
    if (/[",\r\n]/.test(str)) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };
  const header = columns.map(escapeCell).join(",");
  const body = rows.map((row) => columns.map((c) => escapeCell(row[c])).join(",")).join("\r\n");
  return body.length > 0 ? `${header}\r\n${body}` : header;
}