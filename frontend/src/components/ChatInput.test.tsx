import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ChatInput from "./ChatInput";

describe("ChatInput", () => {
  it("submits the trimmed question and clears the input", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ChatInput disabled={false} onSubmit={onSubmit} />);

    const input = screen.getByPlaceholderText(/ask a question/i);
    await user.type(input, "  What was Q3 revenue?  ");
    await user.click(screen.getByRole("button", { name: /ask/i }));

    expect(onSubmit).toHaveBeenCalledWith("What was Q3 revenue?");
    expect(input).toHaveValue("");
  });

  it("does not submit an empty or whitespace-only question", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ChatInput disabled={false} onSubmit={onSubmit} />);

    await user.type(screen.getByPlaceholderText(/ask a question/i), "   ");
    await user.click(screen.getByRole("button", { name: /ask/i }));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("disables the input and button while a question is in flight", () => {
    render(<ChatInput disabled={true} onSubmit={vi.fn()} />);

    expect(screen.getByPlaceholderText(/ask a question/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /asking/i })).toBeDisabled();
  });
});
