import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ClarifyPrompt } from "@/components/ClarifyPrompt";

describe("ClarifyPrompt", () => {
  it("submits options and free text and describes the accessible input", async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    render(<ClarifyPrompt question="Which season?" options={["2024-25"]} onAnswer={onAnswer} />);

    const input = screen.getByRole("textbox", { name: "Your answer" });
    expect(input).toHaveFocus();
    expect(input).toHaveAccessibleDescription("Which season?");

    await user.click(screen.getByRole("button", { name: "2024-25" }));
    await user.type(input, "2023-24{Enter}");

    expect(onAnswer).toHaveBeenNthCalledWith(1, "2024-25");
    expect(onAnswer).toHaveBeenNthCalledWith(2, "2023-24");
  });

  it("focuses the input for each newly received question", () => {
    const { rerender } = render(
      <ClarifyPrompt question="First question?" options={["First option"]} onAnswer={vi.fn()} />,
    );
    const input = screen.getByRole("textbox", { name: "Your answer" });
    expect(input).toHaveFocus();

    screen.getByRole("button", { name: "First option" }).focus();
    expect(input).not.toHaveFocus();
    rerender(<ClarifyPrompt question="Second question?" options={["First option"]} onAnswer={vi.fn()} />);
    expect(input).toHaveFocus();
  });
});
