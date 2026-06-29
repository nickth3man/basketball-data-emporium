import Link from "next/link";

import { EmptyState } from "@/components/empty-state";

/**
 * Route-level not-found UI for `/players/[identifier]`.
 *
 * Server component — no hooks, no client state. Triggered by Next.js
 * when a route under `/players/[identifier]` calls `notFound()` or
 * when the dynamic segment doesn't resolve to a known player.
 */
export default function PlayerNotFound() {
  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto flex min-h-screen max-w-2xl items-center px-4 py-12">
        <div className="w-full space-y-4">
          <EmptyState
            title="Player not found"
            detail="We couldn't find that player. Search again?"
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
