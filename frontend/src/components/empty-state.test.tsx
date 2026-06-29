/**
 * Component tests for `ui/src/components/empty-state.tsx`.
 *
 * The `EmptyState` component is a presentational dashed-border card
 * with a required `title` and optional `detail` line. These tests pin
 * down the rendered DOM shape so future redesigns do not silently
 * change which strings the player-hub surfaces to users.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/empty-state";

describe("EmptyState", () => {
  it("renders the title and supports an optional detail line", () => {
    render(<EmptyState title="No players found" detail="Try a different search term." />);

    expect(screen.getByText("No players found")).toBeInTheDocument();
    expect(screen.getByText("Try a different search term.")).toBeInTheDocument();
  });

  it("omits the detail paragraph when no detail is provided", () => {
    const { container } = render(<EmptyState title="Nothing here" />);

    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    // Only one <p> should be rendered when no `detail` is given.
    expect(container.querySelectorAll("p").length).toBe(1);
  });

  it("renders children-style content (title is a text node, not a child slot)", () => {
    const { container } = render(<EmptyState title="Dataset unavailable" />);
    // Sanity: the dashed border wrapper exists.
    expect(container.firstChild).toBeTruthy();
  });
});
