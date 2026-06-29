"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/button";
import { EmptyState } from "@/components/empty-state";
import { TypedApiError, isRateLimited } from "@/lib/api-errors";

interface PlayerErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Route-level error boundary for `/players/[identifier]`.
 *
 * Auto-wrapped by Next.js — invoked when an error escapes the player-hub
 * component tree. Must be a client component (Next.js requirement for
 * `error.tsx`).
 *
 * Branches on the `TypedApiError.code`:
 * - `rate_limit_jailed` → countdown + disabled retry button
 * - `invalid_player` → tailored message + back link
 * - other `TypedApiError` codes → server-supplied message + retry
 * - unknown errors → generic message (no raw-text leak)
 */
export default function PlayerError({ error, reset }: PlayerErrorProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  if (isRateLimited(error)) {
    return <RateLimitCard error={error} reset={reset} />;
  }

  if (error instanceof TypedApiError) {
    if (error.code === "invalid_player") {
      return (
        <ErrorCard
          title="Player not found"
          detail="We couldn't find that player. It may have been removed or the identifier is wrong."
          showBackLink
          onRetry={reset}
        />
      );
    }
    // Other TypedApiError codes (schema_drift, internal_error, etc.) —
    // the server-supplied message is safe to display verbatim.
    return <ErrorCard title="Something went wrong" detail={error.message} onRetry={reset} />;
  }

  // Unknown error class — don't leak the raw message.
  return (
    <ErrorCard
      title="Something went wrong"
      detail="An unexpected error occurred. Please try again."
      onRetry={reset}
    />
  );
}

interface ErrorCardProps {
  title: string;
  detail: string;
  showBackLink?: boolean;
  onRetry: () => void;
  retryLabel?: string;
  retryDisabled?: boolean;
}

function ErrorCard({
  title,
  detail,
  showBackLink = false,
  onRetry,
  retryLabel = "Try again",
  retryDisabled = false,
}: ErrorCardProps) {
  return (
    <main className="min-h-screen bg-court-paper">
      <div className="mx-auto flex min-h-screen max-w-2xl items-center px-4 py-12">
        <div className="w-full space-y-4">
          <EmptyState title={title} detail={detail} />
          <div className="flex flex-wrap gap-2">
            {showBackLink ? (
              <Link
                href="/players"
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-court-line bg-white px-4 text-sm font-medium text-court-ink transition-colors hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-court-accent"
              >
                Back to players
              </Link>
            ) : null}
            <Button onClick={onRetry} variant="primary" disabled={retryDisabled}>
              {retryLabel}
            </Button>
          </div>
        </div>
      </div>
    </main>
  );
}

/**
 * Rate-limit error card. Shows a per-second countdown derived from the
 * server-supplied `retryAfter` and disables the retry button until the
 * countdown elapses. If `retryAfter` is absent, the button is enabled
 * immediately.
 */
function RateLimitCard({ error, reset }: { error: TypedApiError; reset: () => void }) {
  const initial = error.retryAfter ?? 0;
  const [remaining, setRemaining] = useState(initial);

  useEffect(() => {
    if (remaining <= 0) return;
    const id = setTimeout(() => setRemaining((r) => r - 1), 1000);
    return () => clearTimeout(id);
  }, [remaining]);

  const disabled = remaining > 0;
  return (
    <ErrorCard
      title="Rate limited"
      detail={
        disabled
          ? `Too many requests. Retry in ${remaining}s.`
          : "Too many requests. You can try again now."
      }
      showBackLink
      onRetry={reset}
      retryLabel={disabled ? `Retry in ${remaining}s` : "Try again"}
      retryDisabled={disabled}
    />
  );
}
