/**
 * App entry. Mounts <App /> wrapped in:
 *   - <ThemeProvider> (next-themes) — forced DARK. The Baller Design
 *     System is dark-only by intent; `forcedTheme` pins the `.dark` class
 *     on <html> and ignores any stored preference, so the scoreboard
 *     aesthetic never flashes to light. The provider is retained (rather
 *     than removed) so the few components that call `useTheme()` (e.g.
 *     AnswerChart's palette re-read) keep resolving to "dark".
 *   - <QueryClientProvider> (TanStack Query) — singleton client from
 *     `lib/queryClient.ts` with chat-shaped defaults (staleTime 30s,
 *     no focus refetch, retry once). Phase 1 hooks (useSessions,
 *     history) plug into this provider; useChatTurn deliberately does
 *     not (it owns its own SSE AsyncGenerator reducer).
 *   - <Tooltip.Provider> (radix-ui) — installed in Phase 0 per the
 *     oracle reconciliation so Phase 1 and Phase 2 can mount tooltip
 *     surfaces (icon-only buttons in MessageBubble / SqlPanel /
 *     ResultTable) without another provider churn. Phase 2 actually
 *     adds the surfaces; this phase only ensures the context exists.
 *   - <AppErrorBoundary> (react-error-boundary) — last line of defense
 *     for any uncaught render error. Granular boundaries inside
 *     MessageBubble land in Phase 2; this top-level one guarantees a
 *     recoverable screen even if the rest of the wiring is wrong.
 *   - <Toaster> (sonner) — non-blocking toasts for copy/export actions.
 *
 * All providers must sit ABOVE <App /> so every component can reach
 * them. The order is intentional:
 *   - ThemeProvider OUTSIDE QueryClientProvider so a query refetch
 *     triggered by a theme change (none today) still sees the new
 *     theme; a Query refetch should not need to await ThemeProvider.
 *   - TooltipProvider inside QueryClientProvider (no functional
 *     dependency, but Tooltip is per-app UI; the Query context only
 *     matters to data hooks).
 *   - AppErrorBoundary is the OUTERMOST application provider so it
 *     catches render errors from any of the inner providers, the
 *     App, and the Toaster.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
import { QueryClientProvider } from "@tanstack/react-query";
import { Tooltip } from "radix-ui";

import { App } from "./App";
import { queryClient } from "./lib/queryClient";
import AppErrorBoundary from "./components/ui/ErrorBoundary";
import "./styles/globals.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found in index.html");
}

createRoot(rootElement).render(
  <StrictMode>
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      forcedTheme="dark"
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <Tooltip.Provider delayDuration={300} skipDelayDuration={150}>
          <AppErrorBoundary>
            <App />
            {/*
              Sonner toaster. `richColors` gives status-tinted toasts; `position`
              keeps them clear of the composer at the bottom. Theme is forced
              dark, so toasts always render on the charcoal canvas.
            */}
            <Toaster richColors closeButton position="bottom-right" theme="dark" />
          </AppErrorBoundary>
        </Tooltip.Provider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
);
