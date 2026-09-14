import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Chat from "./Chat";
import { ApiError, postQueryAnswer } from "../api/client";
import type { AnswerResponse } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, postQueryAnswer: vi.fn() };
});

const mockedPostQueryAnswer = vi.mocked(postQueryAnswer);

function makeAnswer(overrides: Partial<AnswerResponse> = {}): AnswerResponse {
  return {
    question: "What was Q3 revenue?",
    statements: [
      {
        text: "Q3 revenue was 100.",
        citations: [
          {
            source_id: "S1",
            document_id: "doc-1",
            document_filename: "budget.xlsx",
            sheet_name: "FCF",
            cell_range: "C42",
            page_number: null,
            reference_number: null,
            text: "Revenue: 100",
          },
        ],
        conflicting: false,
      },
    ],
    sources: [],
    has_conflicts: false,
    grounded: true,
    session_id: "session-1",
    needs_clarification: false,
    clarification_question: null,
    ...overrides,
  };
}

describe("Chat", () => {
  beforeEach(() => {
    mockedPostQueryAnswer.mockReset();
  });

  it("renders the question, then the answer, and carries the session_id into the next request", async () => {
    const user = userEvent.setup();
    mockedPostQueryAnswer.mockResolvedValueOnce(makeAnswer());
    render(<Chat onCitationClick={vi.fn()} />);

    await user.type(screen.getByPlaceholderText(/ask a question/i), "What was Q3 revenue?");
    await user.click(screen.getByRole("button", { name: /ask/i }));

    expect(screen.getByText("What was Q3 revenue?")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Q3 revenue was 100.")).toBeInTheDocument());

    mockedPostQueryAnswer.mockResolvedValueOnce(makeAnswer({ question: "And Q4?" }));
    await user.type(screen.getByPlaceholderText(/ask a question/i), "And Q4?");
    await user.click(screen.getByRole("button", { name: /ask/i }));

    await waitFor(() => expect(mockedPostQueryAnswer).toHaveBeenCalledTimes(2));
    expect(mockedPostQueryAnswer.mock.calls[1][0]).toMatchObject({
      question: "And Q4?",
      session_id: "session-1",
    });
  });

  it("shows a friendly message and does not crash when the API call fails", async () => {
    const user = userEvent.setup();
    mockedPostQueryAnswer.mockRejectedValueOnce(new ApiError("boom", "network"));
    render(<Chat onCitationClick={vi.fn()} />);

    await user.type(screen.getByPlaceholderText(/ask a question/i), "What was Q3 revenue?");
    await user.click(screen.getByRole("button", { name: /ask/i }));

    await waitFor(() =>
      expect(screen.getByText(/couldn't reach the findoc retriever api/i)).toBeInTheDocument()
    );
  });
});
