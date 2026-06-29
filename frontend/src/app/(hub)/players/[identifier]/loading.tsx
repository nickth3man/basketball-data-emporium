import { LoadingBlock } from "@/components/loading-block";

/**
 * Route-level loading UI for `/players/[identifier]`.
 *
 * Auto-wrapped by Next.js as the Suspense fallback while the page's
 * server component is resolving the dynamic `identifier` segment and
 * fetching the initial player payload.
 */
export default function PlayerLoading() {
  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <LoadingBlock label="Loading player…" minHeight="h-64" />
      </div>
    </main>
  );
}
