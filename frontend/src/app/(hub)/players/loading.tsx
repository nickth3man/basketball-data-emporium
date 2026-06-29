import { LoadingBlock } from "@/components/loading-block";

/**
 * Route-level loading UI for `/players` (the search/landing page).
 *
 * Auto-wrapped by Next.js as the Suspense fallback while the page's
 * server component is rendering. Kept as a server component — no hooks,
 * no client state — so it streams immediately.
 */
export default function PlayersLoading() {
  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <LoadingBlock label="Loading players…" minHeight="h-64" />
      </div>
    </main>
  );
}
