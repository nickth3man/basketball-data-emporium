"use client";

import { LoaderCircle, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/button";
import { usePlayerSearch } from "@/features/player-hub/api/queries";
import { useUrlParam } from "@/lib/use-url-param";

interface PlayerSearchProps {
  compact?: boolean;
}

const DEBOUNCE_MS = 250;

/**
 * Player search input with debounced lookup, inline loading affordance,
 * empty-results messaging, and `?term=` URL persistence.
 *
 * Debounce strategy: a 250ms `setTimeout` per keystroke commits the raw
 * `term` to `debouncedTerm`, which is the value actually fed to the
 * `usePlayerSearch` query. While the debounce is pending (`term !==
 * debouncedTerm`) or the query is in flight (`query.isFetching`), the
 * results panel renders a "Searching…" affordance — so the user never
 * sees stale or empty results from a previous term during the wait.
 *
 * URL persistence: on mount we read `?term=` from the URL via
 * `useUrlParam().get` and prepopulate the input; on submit we write the
 * trimmed term back to `?term=`. The mount-time read uses a ref to
 * capture the initial `get` closure because `useUrlParam` returns
 * freshly-bound `get`/`set` on every render — the URL is intentionally
 * read once, not tracked as it changes.
 */
export function PlayerSearch({ compact = false }: PlayerSearchProps) {
  const [term, setTerm] = useState("");
  const [debouncedTerm, setDebouncedTerm] = useState("");
  const router = useRouter();
  const { get: getParam, set: setParam } = useUrlParam();

  // Capture the latest `get` reference so the mount-effect can read
  // the URL exactly once without re-running on every render (the
  // `useUrlParam` hook rebinds its closures on each call). The ref is
  // refreshed in an effect to comply with the React refs rule.
  const getParamRef = useRef(getParam);
  useEffect(() => {
    getParamRef.current = getParam;
  });

  // Debounce: commit `term` to `debouncedTerm` after a quiet period.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedTerm(term), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [term]);

  // Initial read from `?term=`. Runs once on mount — see JSDoc above.
  useEffect(() => {
    const fromUrl = getParamRef.current("term");
    if (fromUrl !== null && fromUrl.length > 0) {
      setTerm(fromUrl);
      setDebouncedTerm(fromUrl);
    }
  }, []);

  const query = usePlayerSearch(debouncedTerm);
  const results = query.data ?? [];
  const trimmedTerm = term.trim();
  const trimmedDebounced = debouncedTerm.trim();
  // "Pending" covers both the debounce delay and an in-flight request.
  const isPending = query.isFetching || trimmedTerm !== trimmedDebounced;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (trimmedTerm.length > 0) {
      setParam("term", trimmedTerm);
    }
    const first = results[0];
    if (first) {
      router.push(`/players/${first.identifier}`);
    }
  }

  return (
    <div className={compact ? "w-full max-w-xl" : "w-full"}>
      <form className="flex min-h-11 gap-2" onSubmit={submit}>
        <label className="relative flex-1">
          <span className="sr-only">Search players</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-court-muted" />
          <input
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder="Search players"
            className="h-11 w-full rounded-md border border-court-line bg-white pl-9 pr-3 text-sm outline-none transition focus:border-court-accent focus:ring-2 focus:ring-teal-100"
          />
        </label>
        <Button type="submit" variant="primary" disabled={results.length === 0}>
          Open
        </Button>
      </form>

      {trimmedTerm.length >= 2 ? (
        <div className="mt-2 max-h-80 overflow-auto rounded-md border border-court-line bg-white shadow-sm">
          {isPending ? (
            <div className="flex items-center gap-2 px-3 py-3 text-sm text-court-muted">
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
              Searching
            </div>
          ) : results.length > 0 ? (
            <div className="divide-y divide-zinc-100">
              {results.map((result) => (
                <button
                  key={result.identifier}
                  type="button"
                  onClick={() => router.push(`/players/${result.identifier}`)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-zinc-50"
                >
                  <span>
                    <span className="block font-medium text-court-ink">{result.name}</span>
                    <span className="text-xs text-court-muted">{result.identifier}</span>
                  </span>
                  <span className="shrink-0 rounded-md bg-zinc-100 px-2 py-1 text-xs text-court-muted">
                    {result.leagues.join("/")}
                  </span>
                </button>
              ))}
            </div>
          ) : trimmedDebounced.length >= 2 ? (
            <div className="px-3 py-3 text-sm text-court-muted">
              No players found for &quot;{trimmedDebounced}&quot;
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
