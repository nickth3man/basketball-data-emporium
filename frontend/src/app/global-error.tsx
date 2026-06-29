"use client";

import { useEffect } from "react";

import { Button } from "@/components/button";

import "./globals.css";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Global error boundary — the last line of defense for the App Router.
 *
 * Replaces the root `layout.tsx` when an error escapes it (or the root
 * template), so it MUST render its own `<html>` and `<body>` tags. The
 * root layout's CSS import is not inherited, so we re-import
 * `globals.css` here to keep the visual baseline.
 *
 * Minimal by design: a centered card with a `reset` button that re-runs
 * the failed segment. Detailed error UI lives in the per-segment
 * `error.tsx` files.
 */
export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <body className="min-h-screen bg-court-paper text-court-ink">
        <main className="flex min-h-screen items-center justify-center px-4">
          <div className="w-full max-w-md rounded-md border border-dashed border-court-line bg-white px-5 py-8 text-sm">
            <p className="font-medium text-court-ink">Application error</p>
            <p className="mt-1 text-court-muted">
              A fatal error occurred. Please try again.
            </p>
            <div className="mt-4">
              <Button onClick={reset} variant="primary">
                Try again
              </Button>
            </div>
          </div>
        </main>
      </body>
    </html>
  );
}
