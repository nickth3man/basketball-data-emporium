/**
 * SQL panel (PLAN §8.3).
 *
 * Collapsible. Renders the validated SQL with `highlight.js` (SQL
 * grammar only — see `globals.css` for the one-time CSS theme import).
 *
 * Collapsed by default per §8.3; users open it explicitly when they want
 * to inspect the query. Copy-to-clipboard uses the modern
 * `navigator.clipboard.writeText` API and degrades gracefully when
 * unavailable (e.g. insecure context); a sonner toast confirms the copy.
 *
 * **A11y structure:** a custom header row carries both the disclosure
 * toggle (a `<button aria-expanded>` styled like the previous
 * `<summary>`) and the Copy button as SIBLINGS — putting a focusable
 * `<button>` inside a `<summary>` triggers axe's `nested-interactive`
 * violation. React state replaces the native `<details>` toggle to keep
 * Copy always visible regardless of expand state.
 *
 * The element is purely presentational: it does not parse or re-execute
 * SQL. highlight.js output is parsed into safe React nodes (no
 * `dangerouslySetInnerHTML`).
 */
import { Fragment, useCallback, useId, useMemo, useState } from "react";
import hljs from "highlight.js/lib/core";
import sqlLang from "highlight.js/lib/languages/sql";
import { Check, ChevronRight, Copy, Database, Terminal } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

if (!hljs.getLanguage("sql")) {
  hljs.registerLanguage("sql", sqlLang);
}

export interface SqlPanelProps {
  sql: string;
}

function highlightToNodes(html: string): React.ReactNode[] {
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
      toast.success("SQL copied to clipboard");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
      toast.error("Couldn't copy — clipboard unavailable");
    }
  }, [sql]);

  const highlighted = useMemo(
    () => highlightToNodes(hljs.highlight(sql, { language: "sql", ignoreIllegals: true }).value),
    [sql],
  );

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-muted/40">
      <header className="flex items-center justify-between gap-2 border-b border-border bg-card/60 px-2 py-1.5">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={codeId}
          onClick={() => setExpanded((v) => !v)}
          className={cn(
            `flex cursor-pointer items-center gap-1.5 rounded-md px-1.5 py-1 text-sm font-medium select-none`,
            `transition-colors hover:bg-muted`,
            `focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none`,
          )}
        >
          <ChevronRight
            className="size-3.5 text-muted-foreground transition-transform duration-200"
            style={{ transform: expanded ? "rotate(90deg)" : undefined }}
            aria-hidden="true"
          />
          <Database className="size-3.5 text-(--color-primary)" aria-hidden="true" />
          <span className="font-display text-xs font-semibold tracking-widest uppercase">SQL</span>
        </button>
        <button
          type="button"
          onClick={() => {
            void handleCopy();
          }}
          className={cn(
            `inline-flex items-center gap-1 rounded-md border border-border`,
            "bg-card px-2 py-1 text-xs font-medium",
            `transition-colors hover:bg-muted`,
            `focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none`,
          )}
          aria-label={copied ? "SQL copied to clipboard" : "Copy SQL to clipboard"}
        >
          {copied ? (
            <Check className="size-3 text-ok-fg" aria-hidden="true" />
          ) : (
            <Copy className="size-3" aria-hidden="true" />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
      </header>
      {expanded && (
        <pre className="m-0 overflow-x-auto px-3 py-2.5 leading-relaxed">
          <code id={codeId} className="hljs language-sql block">
            {highlighted}
          </code>
        </pre>
      )}
      {!expanded && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 text-[0.7rem] text-muted-foreground">
          <Terminal className="size-3" aria-hidden="true" />
          <span className="truncate font-mono">
            {sql.replace(/\s+/g, " ").trim().slice(0, 80)}
            {sql.length > 80 ? "…" : ""}
          </span>
        </div>
      )}
    </section>
  );
}
