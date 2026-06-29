import { LoadingBlock } from "@/components/loading-block";

/**
 * Route-level loading UI for `/teams/[identifier]`.
 *
 * Auto-wrapped by Next.js as the Suspense fallback while the page's
 * server component is resolving the dynamic `identifier` segment and
 * fetching the initial team payload.
 */
export default function TeamLoading() {
  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <LoadingBlock label="Loading team…" minHeight="h-64" />
      </div>
    </main>
  );
}
