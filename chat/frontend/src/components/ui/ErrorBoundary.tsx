/**
 * Top-level + granular error boundaries (Phase 0 foundation).
 *
 * Two exports, both thin wrappers over `react-error-boundary`:
 *
 *   - `AppErrorBoundary` (default export) — the app's last line of
 *     defense. Mounted once in `main.tsx`, directly outside `<App />`.
 *     If a render error escapes the tree, the user gets a full-card
 *     "Something went wrong" screen with a Reload button (hard reset
 *     because the only safe action when the root tree is corrupted is
 *     a clean reload) and a Try-again button (calls
 *     `resetErrorBoundary`, which re-renders the children in place).
 *
 *   - `GranularBoundary` (named export) — a compact fallback intended
 *     to wrap individual rich-panel clusters inside an assistant
 *     message bubble. NOT placed by this phase; Phase 2 mounts it
 *     around the rich-panels cluster (MarkdownContent / AnswerChart /
 *     ResultTable, etc.) so a single panel's render failure doesn't
 *     take down the whole bubble. Accepts an optional `label` so the
 *     fallback can name the section ("SQL", "Chart", "Table", …).
 *
 * Styling uses the Baller token CSS vars defined in `globals.css`
 * (`--color-card`, `--color-foreground`, `--color-primary`, …) via the
 * same `bg-[color:var(--…)]` arbitrary-value pattern used throughout
 * the codebase. We deliberately do NOT add a new utility class — the
 * existing convention is enough for two surfaces.
 *
 * Accessibility:
 *   - The fallback is wrapped in `role="alert"` so screen readers
 *     announce the error on appearance (live = assertive — appropriate
 *     for an unexpected failure, not a status update).
 *   - Reload + Try-again are real `<button type="button">` elements
 *     with visible focus rings (inherited from the existing focus-
 *     visible tokens), so keyboard users can recover without a mouse.
 */
import type { ReactNode } from "react";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { clsx } from "clsx";

interface GranularBoundaryProps {
  children: ReactNode;
  /** Optional section name rendered in the compact fallback (e.g. "SQL", "Chart"). */
  label?: string;
}

/**
 * Compact inline fallback for a single rich-panel cluster. Renders a
 * small bordered card with a "Couldn't render this section." line and
 * a Try-again button. Stays in-flow — doesn't blow the layout up to
 * the full card size.
 */
function GranularFallback({ label, resetErrorBoundary }: FallbackProps & { label?: string }) {
  const heading = label ? `Couldn't render the ${label} section.` : "Couldn't render this section.";
  return (
    <div
      role="alert"
      className={clsx(
        "flex items-center justify-between gap-3 rounded-lg border px-3 py-2",
        `border-danger-border bg-danger-bg`,
        "text-sm text-(--color-foreground)",
      )}
    >
      <span>{heading}</span>
      <button
        type="button"
        onClick={resetErrorBoundary}
        className={clsx(
          "shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium",
          "border-border bg-card",
          `text-(--color-foreground)`,
          `hover:bg-muted`,
          "focus-visible:ring-2 focus-visible:outline-none",
          `focus-visible:ring-(--color-ring) focus-visible:ring-offset-2`,
          "focus-visible:ring-offset-background",
          "disabled:opacity-50",
        )}
      >
        Try again
      </button>
    </div>
  );
}

/**
 * Granular boundary — wrap a single rich-panel cluster (e.g. the
 * charts/tables block inside an assistant bubble) to keep a render
 * failure from killing the rest of the bubble. Phase 2 mounts these
 * inside `MessageBubble.tsx`; this phase only ships the primitive.
 */
function GranularBoundary({ children, label }: GranularBoundaryProps) {
  return (
    <ErrorBoundary fallbackRender={(props) => <GranularFallback {...props} label={label} />}>
      {children}
    </ErrorBoundary>
  );
}
// Suppress noUnusedLocals — retained for Phase 2 integration.
void GranularBoundary;

/**
 * Full-card fallback for the top-level app boundary. Two affordances:
 *
 *   - "Reload"  → `window.location.reload()` (hard reset, the safe
 *      choice when the entire React tree may be in a bad state).
 *   - "Try again" → `resetErrorBoundary()` (re-renders children in
 *      place, no network reload). Useful when the user suspects the
 *      error was transient and doesn't want to lose their in-flight
 *      composer text or scroll position.
 */
function AppFallback({ resetErrorBoundary }: FallbackProps) {
  return (
    <div
      role="alert"
      className={clsx(
        "flex min-h-screen w-full items-center justify-center px-6 py-12",
        "bg-background",
      )}
    >
      <div
        className={clsx(
          "w-full max-w-md rounded-2xl border p-8 text-center",
          "border-border bg-card",
          "text-(--color-foreground) shadow-lg",
        )}
      >
        <p
          className={clsx(
            "mb-2 font-display text-2xl tracking-wide uppercase",
            "text-(--color-foreground)",
          )}
        >
          Something went wrong
        </p>
        <p className={clsx("mb-6 text-sm", `text-muted-foreground`)}>
          The chat hit an unexpected error.
        </p>
        <div className="flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => {
              window.location.reload();
            }}
            className={clsx(
              "rounded-full px-4 py-2 text-sm font-medium",
              `bg-(--color-primary) text-primary-foreground`,
              "shadow-sm shadow-(color:--color-primary)/20",
              `hover:brightness-110`,
              `active:brightness-95`,
              "focus-visible:ring-2 focus-visible:outline-none",
              `focus-visible:ring-(--color-ring) focus-visible:ring-offset-2`,
              "focus-visible:ring-offset-background",
            )}
          >
            Reload
          </button>
          <button
            type="button"
            onClick={resetErrorBoundary}
            className={clsx(
              "rounded-full border px-4 py-2 text-sm font-medium",
              "border-border bg-transparent",
              `text-(--color-foreground)`,
              `hover:bg-muted`,
              "focus-visible:ring-2 focus-visible:outline-none",
              `focus-visible:ring-(--color-ring) focus-visible:ring-offset-2`,
              "focus-visible:ring-offset-background",
            )}
          >
            Try again
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Top-level app boundary. Wrap the entire `<App />` in main.tsx so
 * any uncaught render error falls back to the full-card screen.
 * `resetErrorBoundary` re-renders the tree in place — `window.location
 * .reload()` is the hard reset for when the tree is truly unrecoverable.
 *
 * Default export because the import site (`main.tsx`) is the canonical
 * consumer; the named `GranularBoundary` is the secondary export used
 * by Phase 2.
 */
export default function AppErrorBoundary({ children }: { children: ReactNode }) {
  return <ErrorBoundary FallbackComponent={AppFallback}>{children}</ErrorBoundary>;
}
