import Link from "next/link";

import { EmptyState } from "@/components/empty-state";

/**
 * Route-level not-found UI for `/teams/[identifier]`.
 *
 * Server component — no hooks, no client state. Triggered by Next.js
 * when a route under `/teams/[identifier]` calls `notFound()` or
 * when the dynamic segment doesn't resolve to a known team.
 */
export default function TeamNotFound() {
  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto flex min-h-screen max-w-2xl items-center px-4 py-12">
        <div className="w-full space-y-4">
          <EmptyState
            title="Team not found"
            detail="We couldn't find that team. Search again?"
          />
          <Link
            href="/teams"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-court-accent px-4 text-sm font-medium text-white transition-colors hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-court-accent"
          >
            Back to teams
          </Link>
        </div>
      </div>
    </main>
  );
}
