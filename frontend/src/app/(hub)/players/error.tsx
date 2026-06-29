"use client";

import { useEffect } from "react";

import { Button } from "@/components/button";
import { EmptyState } from "@/components/empty-state";
import { TypedApiError } from "@/lib/api-errors";

interface PlayersErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Route-level error boundary for `/players` (the search/landing page).
 *
 * Auto-wrapped by Next.js — invoked when a server or client error escapes
 * the page's component tree. Must be a client component (Next.js
 * requirement for `error.tsx`).
 */
export default function PlayersError({ error, reset }: PlayersErrorProps) {
  useEffect(() => {
    // Surface to the browser console + Next.js error overlay.
    console.error(error);
  }, [error]);

  // Only echo the raw message for `TypedApiError` — the server-supplied
  // text is safe to display. For unknown errors, show a generic message
  // so we don't leak internal details.
  const detail =
    error instanceof TypedApiError
      ? error.message
      : "An unexpected error occurred. Please try again.";

  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto flex min-h-screen max-w-2xl items-center px-4 py-12">
        <div className="w-full space-y-4">
          <EmptyState title="Something went wrong" detail={detail} />
          <Button onClick={reset} variant="primary">
            Try again
          </Button>
        </div>
      </div>
    </main>
  );
}
