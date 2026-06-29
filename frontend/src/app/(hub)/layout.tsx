import type { ReactNode } from "react";

import { HubNav } from "@/components/hub-nav";

/**
 * Route-group layout for every hub page (`/players/*` and `/teams/*`).
 *
 * Next.js App Router treats the `(hub)` segment as a layout-only group
 * — the parentheses are stripped from the URL, so `/players/jamesle01`
 * still resolves to `app/(hub)/players/[identifier]/page.tsx`. The nav
 * therefore only appears on hub pages and never on the root redirect
 * (`/` → `/players`) or the global not-found page.
 */
export default function HubLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <>
      <HubNav />
      {children}
    </>
  );
}
