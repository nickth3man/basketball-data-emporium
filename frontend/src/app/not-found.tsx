import Link from "next/link";

import { EmptyState } from "@/components/empty-state";

/**
 * Global not-found UI — rendered by Next.js for every route that does
 * not match a segment in the App Router. Catches paths like
 * `/nonexistent`, `/foo/bar`, or `/players/` (with trailing slash, after
 * Next.js's normalisation) — anything that does not resolve to a
 * concrete page.
 *
 * The per-segment `app/players/[identifier]/not-found.tsx` and
 * `app/teams/[identifier]/not-found.tsx` files only fire when a route
 * *under* their subtree calls `notFound()` — they never overlap with
 * this global fallback. A truly unmatched path always lands here.
 *
 * Server component — no hooks, no client state. The copy and CTA mirror
 * the per-segment pages so the global 404 is visually consistent.
 */
export default function NotFound() {
  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto flex min-h-screen max-w-2xl items-center px-4 py-12">
        <div className="w-full space-y-4">
          <EmptyState
            title="Page not found"
            detail="We couldn't find that page. Try searching for a player or team."
          />
          <Link
            href="/players"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-court-accent px-4 text-sm font-medium text-white transition-colors hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-court-accent"
          >
            Back to players
          </Link>
        </div>
      </div>
    </main>
  );
}
