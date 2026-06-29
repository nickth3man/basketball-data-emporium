/**
 * Hand-curated fallback list of featured players for the "Featured players"
 * sidebar on the players landing page.
 *
 * Identifiers MUST match the canonical Basketball Reference player IDs
 * already used by the rest of the app (e.g. the live API, existing
 * fixtures, and deep links).
 *
 * The backend exposes the same curated list through
 * `GET /api/players/featured`; the UI keeps this constant as its offline /
 * error fallback so the sidebar never empties.
 *
 * Endpoint shape:
 *   GET /api/players/featured
 *     → { athletes: FeaturedAthlete[] }
 *   type FeaturedAthlete = {
 *     identifier: string;   // Basketball Reference player id
 *     name: string;         // display name
 *     blurb?: string;       // optional sidebar blurb
 *     leagues: string[];    // e.g. ["NBA"]
 *   }
 *
 * Decision needed:
 *   - Per-league (NBA-only today) or multi-league (one list per league,
 *     league selector in the sidebar)?
 *   - Personalized (per-user) or global (same list for everyone)?
 *     The current constant is a single global list, so a global v1 is
 *     the smallest delta.
 *
 * Verify: after wiring, `npm run dev` + navigate to `/players` and
 *   confirm the sidebar still renders the four canonical players
 *   (LeBron, MJ, Curry, Bird) when the endpoint is reachable, and
 *   continues to render them when it returns 500.
 */
export interface SampleAthlete {
  identifier: string;
  name: string;
  /** Optional blurb shown under the name in the featured sidebar. */
  blurb?: string;
}

export const SAMPLE_ATHLETES: readonly SampleAthlete[] = [
  // TODO P2-FE-04: validate these fallback slugs against the live backend in
  // CI. The static list is useful offline, but every identifier should also
  // resolve through `/api/players/search` or a direct API contract check.
  { identifier: "jamesle01", name: "LeBron James", blurb: "All-time scoring leader" },
  { identifier: "jordami01", name: "Michael Jordan" },
  { identifier: "curryst01", name: "Stephen Curry" },
  { identifier: "birdla01", name: "Larry Bird" },
] as const;
