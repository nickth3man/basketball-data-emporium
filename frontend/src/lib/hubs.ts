/**
 * Cross-hub navigation config.
 *
 * Each entry maps to one logical "hub" in the app (player workspace,
 * team workspace, …). Adding a new hub is a one-line change here plus
 * a new `<Link>`/page segment — the nav component reads the list at
 * render time, no JSX edits required.
 *
 * The labels are intentionally short (one word) because the nav is a
 * horizontal pill row; longer copy belongs on the hub's own page header.
 */
export interface HubLink {
  label: string;
  href: string;
}

export const HUBS: readonly HubLink[] = [
  { label: "Players", href: "/players" },
  { label: "Teams", href: "/teams" },
] as const;
