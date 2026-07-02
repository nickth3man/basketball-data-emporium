// Human-readable labels for award types. Server-side award queries expose
// selection rows plus major-award winners from the BBR staging tables.
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

// Awards we show as honors/badges.
export const HONOR_AWARD_TYPES = new Set(["All-NBA", "All-Rookie", "All-Defense", "All-Star"]);

export function labelAwardType(type: string): string {
  return HONOR_LABELS[type] ?? type;
}
