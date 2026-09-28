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

  const notice = "Part of the relevant information is restricted and was excluded from this answer.";

  it("shows the restriction notice as a banner, not as a statement, alongside the answer", () => {
    render(
      <AnswerMessage
        answer={{
          ...baseAnswer,
          grounded: true,
          restriction_notice: notice,
          statements: [
            { text: "Q3 revenue was 100.", citations: [citation], conflicting: false },
            { text: notice, citations: [], conflicting: false, notice: true },
          ],
        }}
        onCitationClick={vi.fn()}
      />
    );
    expect(screen.getByText("Q3 revenue was 100.")).toBeInTheDocument();
    // Rendered exactly once — in the banner, not repeated as a statement.
    expect(screen.getAllByText(new RegExp(notice))).toHaveLength(1);
  });

  it("shows only the restriction notice when every relevant source was restricted", () => {
    render(
      <AnswerMessage
        answer={{
          ...baseAnswer,
          grounded: true,
          restriction_notice: notice,
          statements: [{ text: notice, citations: [], conflicting: false, notice: true }],
        }}
        onCitationClick={vi.fn()}
      />
    );
    expect(screen.getByText(new RegExp(notice))).toBeInTheDocument();
    expect(screen.queryByText(/couldn't find a reliable answer/i)).not.toBeInTheDocument();
  });

  it("says which version the answer used, and flags a non-current one", () => {
    render(
      <AnswerMessage
        answer={{
          ...baseAnswer,
          grounded: true,
          statements: [{ text: "Q3 revenue was 90.", citations: [citation], conflicting: false }],
          versions_used: [
            {
              document_id: "doc-1",
              filename: "Budget_v1.xlsx",
              version_number: 1,
              approval_status: "approved",
              is_current: false,
              current_version_filename: "Budget_v2.xlsx",
            },
          ],
        }}
        onCitationClick={vi.fn()}
      />
    );
    expect(screen.getByText(/which is not the/i)).toHaveTextContent(
      "This answer uses Budget_v1.xlsx (v1), which is not the current version — the current one is Budget_v2.xlsx."
    );
    expect(screen.getByText(/based on/i)).toHaveTextContent("Budget_v1.xlsx (v1, approved)");
  });

  it("shows the version used without a warning when it is the current one", () => {
    render(
      <AnswerMessage
        answer={{
          ...baseAnswer,
          grounded: true,
          statements: [{ text: "Q3 revenue was 100.", citations: [citation], conflicting: false }],
          versions_used: [
            {
              document_id: "doc-2",
              filename: "Budget_v2.xlsx",
              version_number: 2,
              approval_status: "approved",
              is_current: true,
              current_version_filename: null,
            },
          ],
        }}
        onCitationClick={vi.fn()}
      />
    );
    expect(screen.getByText(/based on/i)).toHaveTextContent("Budget_v2.xlsx (v2, approved, current)");
    expect(screen.queryByText(/not the current version/i)).not.toBeInTheDocument();
  });
});
