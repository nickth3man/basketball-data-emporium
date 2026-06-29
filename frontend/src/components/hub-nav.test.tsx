/**
 * Component tests for `ui/src/components/hub-nav.tsx`.
 *
 * `HubNav` reads `usePathname()` from `next/navigation` to mark the
 * active hub. We mock that hook per-test to control which entry the
 * pill highlights. The static `HUBS` constant is verified separately
 * in `ui/src/lib/hubs.test.ts`.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { usePathname } = vi.hoisted(() => ({ usePathname: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname,
}));

import { HubNav } from "@/components/hub-nav";
import { HUBS } from "@/lib/hubs";

describe("HubNav", () => {
  afterEach(() => {
    usePathname.mockReset();
  });

  it("renders a link for every hub in HUBS", () => {
    usePathname.mockReturnValue("/");

    render(<HubNav />);

    for (const hub of HUBS) {
      expect(screen.getByRole("link", { name: hub.label })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: hub.label })).toHaveAttribute("href", hub.href);
    }
  });

  it("marks the active hub via data-active and aria-current", () => {
    // Drilling into a player workspace — `Players` should be the
    // active pill because its href is a prefix of the current path.
    usePathname.mockReturnValue("/players/jamesle01");

    render(<HubNav />);

    const players = screen.getByRole("link", { name: "Players" });
    const teams = screen.getByRole("link", { name: "Teams" });

    expect(players).toHaveAttribute("data-active");
    expect(players).toHaveAttribute("aria-current", "page");
    expect(teams).not.toHaveAttribute("data-active");
    expect(teams).not.toHaveAttribute("aria-current");
  });

  it("marks a hub active on exact-path matches", () => {
    usePathname.mockReturnValue("/teams");

    render(<HubNav />);

    const players = screen.getByRole("link", { name: "Players" });
    const teams = screen.getByRole("link", { name: "Teams" });

    expect(teams).toHaveAttribute("data-active");
    expect(players).not.toHaveAttribute("data-active");
  });

  it("marks no hub active on an unrelated path (e.g. /nonexistent)", () => {
    usePathname.mockReturnValue("/nonexistent");

    render(<HubNav />);

    for (const hub of HUBS) {
      const link = screen.getByRole("link", { name: hub.label });
      expect(link).not.toHaveAttribute("data-active");
    }
  });
});
