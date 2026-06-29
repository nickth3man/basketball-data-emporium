/**
 * Re-export shim — the canonical implementations live in `@/lib/format`
 * (a feature-agnostic module that both player-hub and team-hub consume).
 *
 * Kept as a re-export for backward compatibility with any caller that
 * still imports the helpers from the historical player-hub path. The
 * long-term plan is to migrate every consumer to `@/lib/format` directly
 * and delete this shim.
 */
export { asNumber, formatStat, formatValue } from "@/lib/format";
