/**
 * SQL panel (PLAN §8.3).
 *
 * Collapsible. Renders the validated SQL with `highlight.js` (SQL
 * grammar only — see `globals.css` for the one-time CSS theme import).
 *
 * Collapsed by default per §8.3; users open it explicitly when they want
 * to inspect the query. Copy-to-clipboard uses the modern
 * `navigator.clipboard.writeText` API and degrades gracefully when
 * unavailable (e.g. insecure context).
 *
 * **A11y structure:** a custom header row carries both the disclosure
 * toggle (a `<button aria-expanded>` styled like the previous
 * `<summary>`) and the Copy button as SIBLINGS — putting a focusable
 * `<button>` inside a `<summary>` triggers axe's `nested-interactive`
 * violation (the `<summary>` itself is keyboard-focusable). React state
 * replaces the native `<details>` toggle to keep Copy always visible
 * regardless of expand state.
 *
 * The element is purely presentational: it does not parse or re-execute
 * SQL. The backend's `validation.py` (§7.4) is the only thing allowed to
 * run SQL, against the template's table allowlist.
 *
 * highlight.js output is a fixed grammar of `<span class="hljs-…">…`
 * wrappers; we parse that HTML into React nodes (no `dangerouslySetInnerHTML`)
 * so the only HTML that reaches the DOM is the `<span>` wrappers we
 * generate ourselves.
 */
import { Fragment, useCallback, useId, useMemo, useState } from "react";
import hljs from "highlight.js/lib/core";
import sqlLang from "highlight.js/lib/languages/sql";

// Register SQL once (idempotent — `registerLanguage` is a no-op on repeat
// calls with the same name, but we still gate on `getLanguage()` so the
// registration doesn't pollute the bundle log).
if (!hljs.getLanguage("sql")) {
  hljs.registerLanguage("sql", sqlLang);
}

export interface SqlPanelProps {
  sql: string;
}

/**
 * Parse highlight.js's HTML output into safe React nodes. hljs emits
 * `<span class="hljs-…">token</span>` wrappers interleaved with raw text;
 * the wrappers never include user-controlled attributes (highlight.js's
 * SQL grammar emits a fixed set of class names), so we can map them onto
 * JSX children with stable, content-based keys.
 */
function highlightToNodes(html: string): React.ReactNode[] {
  // Split keeping the wrapper tokens. The regex matches the full span
  // including its class attribute; the inner text is restricted to
  // characters highlight.js would ever emit (no `<`).
  const tokenRe = /<span class="(hljs-[a-z0-9-]+)">([^<]*)<\/span>/g;
  const nodes: React.ReactNode[] = [];
  const matches = Array.from(html.matchAll(tokenRe));
  let cursor = 0;
  for (let i = 0; i < matches.length; i++) {
    const match = matches[i];
    if (match === undefined) continue;
    const idx = match.index ?? 0;
    if (idx > cursor) {
      nodes.push(<Fragment key={`t-${i}`}>{html.slice(cursor, idx)}</Fragment>);
    }
    nodes.push(
      <span key={`s-${i}`} className={match[1]}>
        {match[2]}
      </span>,
    );
    cursor = idx + match[0].length;
  }
  if (cursor < html.length) {
    nodes.push(<Fragment key={`e-${matches.length}`}>{html.slice(cursor, html.length)}</Fragment>);
  }
  return nodes;
}

export function SqlPanel({ sql }: SqlPanelProps) {
  const [expanded, setExpanded] = useState<boolean>(false);
  const [copied, setCopied] = useState<boolean>(false);
  const codeId = useId();

  const handleCopy = useCallback(async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can be unavailable in insecure contexts or when
      // permissions are denied. We intentionally swallow the error —
      // the panel still renders the SQL for manual selection.
      setCopied(false);
    }
  }, [sql]);

  const highlighted = useMemo(
    () => highlightToNodes(hljs.highlight(sql, { language: "sql", ignoreIllegals: true }).value),
    [sql],
  );

  return (
    <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)]">
      <header className="flex items-center justify-between gap-2 border-b border-[color:var(--color-border)] px-3 py-2">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={codeId}
          onClick={() => setExpanded((v) => !v)}
          className="flex cursor-pointer items-center gap-2 text-sm font-medium select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)]"
        >
          <span aria-hidden="true" className="text-xs">
            {expanded ? "▾" : "▸"}
          </span>
          SQL
        </button>
        <button
          type="button"
          onClick={() => {
            void handleCopy();
          }}
          className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-background)] px-2 py-0.5 text-xs font-medium hover:bg-[color:var(--color-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)]"
          aria-label={copied ? "SQL copied to clipboard" : "Copy SQL to clipboard"}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </header>
      {expanded && (
        <pre className="m-0 overflow-x-auto px-3 pb-3 pt-1 text-xs leading-relaxed">
          <code id={codeId} className="hljs language-sql block">
            {highlighted}
          </code>
        </pre>
      )}
    </section>
  );
}
