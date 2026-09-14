import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AnswerMessage from "./AnswerMessage";
import type { AnswerResponse, CitationOut } from "../api/types";

const baseAnswer: AnswerResponse = {
  question: "What was Q3 revenue?",
  statements: [],
  sources: [],
  has_conflicts: false,
  grounded: false,
  session_id: "session-1",
  needs_clarification: false,
  clarification_question: null,
};

const citation: CitationOut = {
  source_id: "S1",
  document_id: "doc-1",
  document_filename: "budget.xlsx",
  sheet_name: "FCF",
  cell_range: "C42",
  page_number: null,
  reference_number: null,
  text: "Free cash flow: 42",
};

describe("AnswerMessage", () => {
  it("shows the clarification prompt when needs_clarification is set (ID-HU-BE-009 contract)", () => {
    render(
      <AnswerMessage
        answer={{
          ...baseAnswer,
          needs_clarification: true,
          clarification_question: "Which entity and period do you mean?",
        }}
        onCitationClick={vi.fn()}
      />
    );
    expect(screen.getByText("Which entity and period do you mean?")).toBeInTheDocument();
  });

  it("shows a fallback empty state when ungrounded", () => {
    render(<AnswerMessage answer={baseAnswer} onCitationClick={vi.fn()} />);
    expect(screen.getByText(/couldn't find a reliable answer/i)).toBeInTheDocument();
  });

  it("renders each statement with its citations when grounded", () => {
    render(
      <AnswerMessage
        answer={{
          ...baseAnswer,
          grounded: true,
          statements: [{ text: "Q3 revenue was 100.", citations: [citation], conflicting: false }],
        }}
        onCitationClick={vi.fn()}
      />
    );
    expect(screen.getByText("Q3 revenue was 100.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /budget\.xlsx/i })).toBeInTheDocument();
  });

  it("shows the conflict banner when has_conflicts is true", () => {
    render(
      <AnswerMessage
        answer={{
          ...baseAnswer,
          grounded: true,
          has_conflicts: true,
          statements: [
            { text: "Source A says 100.", citations: [citation], conflicting: true },
            { text: "Source B says 90.", citations: [citation], conflicting: true },
          ],
        }}
        onCitationClick={vi.fn()}
      />
    );
    expect(screen.getByText(/sources disagree/i)).toBeInTheDocument();
  });
});
