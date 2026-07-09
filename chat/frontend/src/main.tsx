/**
 * App entry. Mounts <App /> wrapped in:
 *   - <ThemeProvider> (next-themes) — forced DARK. The Baller Design
 *     System is dark-only by intent; `forcedTheme` pins the `.dark` class
 *     on <html> and ignores any stored preference, so the scoreboard
 *     aesthetic never flashes to light. The provider is retained (rather
 *     than removed) so the few components that call `useTheme()` (e.g.
 *     AnswerChart's palette re-read) keep resolving to "dark".
 *   - <Toaster> (sonner) — non-blocking toasts for copy/export actions.
 *
 * Both providers must sit ABOVE <App /> so every component can reach them.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";

import { App } from "./App";
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
      <App />
      {/*
        Sonner toaster. `richColors` gives status-tinted toasts; `position`
        keeps them clear of the composer at the bottom. Theme is forced
        dark, so toasts always render on the charcoal canvas.
      */}
      <Toaster richColors closeButton position="bottom-right" theme="dark" />
    </ThemeProvider>
  </StrictMode>,
);
