/**
 * App entry. Mounts <App /> wrapped in:
 *   - <ThemeProvider> (next-themes) — flash-free light/dark via class toggle,
 *     defaulting to the OS preference.
 *   - <Toaster> (sonner) — non-blocking toasts for copy/export actions.
 *
 * Both providers must sit ABOVE <App /> so every component (header toggle,
 * copy handlers, command palette) can reach them.
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
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <App />
      {/*
        Sonner toaster. `richColors` gives status-tinted toasts; `position`
        keeps them clear of the composer at the bottom. Theme is wired to
        next-themes' resolved value so dark sessions get dark toasts.
      */}
      <Toaster richColors closeButton position="bottom-right" />
    </ThemeProvider>
  </StrictMode>,
);
