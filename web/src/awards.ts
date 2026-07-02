// Human-readable labels for award types. fact_player_awards contains both
// selection rows (Title-case) and voting-share rows (lower-case) for the same
// award; the UI consolidates on the Title-case rows and hides raw voting rows.
export const HONOR_LABELS: Record<string, string> = {
  "All-NBA": "All-NBA",
  "All-Rookie": "All-Rookie",
  "All-Defense": "All-Defense",
  "All-Star": "All-Star",
  "nba mvp": "MVP",
  "nba roy": "ROY",
  "nba dpoy": "DPOY",
  "nba mip": "MIP",
  "nba smoy": "SMOY",
};

// Awards we show as honors/badges. Lower-case voting rows are filtered out.
export const HONOR_AWARD_TYPES = new Set(["All-NBA", "All-Rookie", "All-Defense", "All-Star"]);

export function labelAwardType(type: string): string {
  return HONOR_LABELS[type] ?? type;
}
