"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { HUBS } from "@/lib/hubs";

/**
 * Cross-hub navigation — a horizontal pill row that links every entry
 * declared in `ui/src/lib/hubs.ts`. Renders the active hub as a filled
 * accent pill (`data-active`) and the others as muted ghost links.
 *
 * Active-state rule: a hub is "active" when the current pathname
 * matches the hub's `href` exactly OR starts with `${href}/` (so
 * `/players/jamesle01` keeps the "Players" pill highlighted while a
 * user drills into a player workspace). The home route `/` is
 * intentionally nav-less — it redirects to `/players` and is rendered
 * by the root layout, not the hub layout.
 *
 * The component is a client component because `usePathname()` is a
 * client hook. The static `HUBS` constant is imported at module scope
 * so the list is inlined into the client bundle — no extra round-trip.
 */
export function HubNav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Hub navigation"
      className="border-b border-court-line bg-white"
    >
      <div className="mx-auto flex max-w-7xl items-center gap-1 px-4 py-2 sm:px-6 lg:px-8">
        {HUBS.map((hub) => {
          const isActive =
            pathname === hub.href || pathname.startsWith(`${hub.href}/`);
          return (
            <Link
              key={hub.href}
              href={hub.href}
              data-active={isActive ? "" : undefined}
              aria-current={isActive ? "page" : undefined}
              className="h-9 rounded-md px-3 text-sm font-medium text-court-muted transition hover:bg-zinc-100 hover:text-court-ink data-active:bg-court-accent data-active:text-white"
            >
              {hub.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
