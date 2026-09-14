import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import CitationBadge from "./CitationBadge";
import type { CitationOut } from "../api/types";

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

describe("CitationBadge", () => {
  it("renders the document/sheet/cell location", () => {
    render(<CitationBadge citation={citation} onClick={vi.fn()} />);
    const badge = screen.getByRole("button");
    expect(badge).toHaveTextContent("budget.xlsx");
    expect(badge).toHaveTextContent("FCF");
    expect(badge).toHaveTextContent("C42");
  });

  it("calls onClick with the citation when clicked", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<CitationBadge citation={citation} onClick={onClick} />);

    await user.click(screen.getByRole("button"));

    expect(onClick).toHaveBeenCalledWith(citation);
  });

  it("renders a page number for PDF citations instead of sheet/cell", () => {
    const pdfCitation: CitationOut = {
      ...citation,
      sheet_name: null,
      cell_range: null,
      page_number: 4,
    };
    render(<CitationBadge citation={pdfCitation} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveTextContent("p.4");
  });
});
