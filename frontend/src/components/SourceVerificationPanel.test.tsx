import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SourceVerificationPanel from "./SourceVerificationPanel";
import { getCell, resolveDocument } from "../api/client";
import type { CitationOut, DocumentOut, DocumentResolutionOut } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    resolveDocument: vi.fn(),
    getCell: vi.fn(),
    documentFileUrl: (id: string) => `http://localhost:8000/documents/${id}/file`,
  };
});

const mockedResolveDocument = vi.mocked(resolveDocument);
const mockedGetCell = vi.mocked(getCell);

function makeDocument(overrides: Partial<DocumentOut> = {}): DocumentOut {
  return {
    id: "doc-1",
    filename: "budget.xlsx",
    doc_type: "excel",
    status: "ready",
    error_message: null,
    warnings: [],
    uploaded_at: "2026-01-01T00:00:00Z",
    is_current: true,
    superseded_by_id: null,
    confidentiality_tag: "public",
    ...overrides,
  };
}

const excelCitation: CitationOut = {
  source_id: "S1",
  document_id: "doc-1",
  document_filename: "budget.xlsx",
  sheet_name: "FCF",
  cell_range: "C42",
  page_number: null,
  reference_number: null,
  text: "Free cash flow: 42",
};

describe("SourceVerificationPanel", () => {
  beforeEach(() => {
    mockedResolveDocument.mockReset();
    mockedGetCell.mockReset();
    // Safe default so tests that don't care about the cell lookup (it fires
    // automatically for any single-cell citation) don't crash on an
    // unconfigured mock returning undefined instead of a Promise.
    mockedGetCell.mockResolvedValue({
      sheet_name: "FCF",
      address: "C42",
      value: null,
      formula: null,
      formula_references: [],
    });
  });

  it("shows the cell's value and formula for a single-cell Excel citation", async () => {
    mockedResolveDocument.mockResolvedValueOnce({
      document: makeDocument(),
      is_superseded: false,
      current_version: null,
      access: { allowed: true, reason: null },
    });
    mockedGetCell.mockResolvedValueOnce({
      sheet_name: "FCF",
      address: "C42",
      value: "42",
      formula: "=B42*1",
      formula_references: ["Assumptions!B5"],
    });

    render(<SourceVerificationPanel citation={excelCitation} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("42")).toBeInTheDocument());
    expect(screen.getByText("=B42*1")).toBeInTheDocument();
    expect(mockedGetCell).toHaveBeenCalledWith("doc-1", "FCF", "C42");
  });

  it("does not attempt a cell lookup for a multi-cell range", async () => {
    mockedResolveDocument.mockResolvedValueOnce({
      document: makeDocument(),
      is_superseded: false,
      current_version: null,
      access: { allowed: true, reason: null },
    });

    render(
      <SourceVerificationPanel
        citation={{ ...excelCitation, cell_range: "A1:B2" }}
        onClose={vi.fn()}
      />
    );

    await waitFor(() => expect(screen.getByText("budget.xlsx")).toBeInTheDocument());
    expect(mockedGetCell).not.toHaveBeenCalled();
  });

  it("shows a warning banner and lets the analyst switch to the current version", async () => {
    const user = userEvent.setup();
    const currentVersion = makeDocument({ id: "doc-2", filename: "budget_v4.xlsx" });
    mockedResolveDocument.mockResolvedValueOnce({
      document: makeDocument({ is_current: false, superseded_by_id: "doc-2" }),
      is_superseded: true,
      current_version: currentVersion,
      access: { allowed: true, reason: null },
    });

    render(<SourceVerificationPanel citation={excelCitation} onClose={vi.fn()} />);

    await waitFor(() =>
      expect(screen.getByText(/newer approved version/i)).toBeInTheDocument()
    );

    mockedResolveDocument.mockResolvedValueOnce({
      document: currentVersion,
      is_superseded: false,
      current_version: null,
      access: { allowed: true, reason: null },
    });
    await user.click(screen.getByRole("button", { name: /view current version/i }));

    await waitFor(() => expect(mockedResolveDocument).toHaveBeenCalledWith("doc-2"));
  });

  it("denies access and hides content for a restricted document", async () => {
    mockedResolveDocument.mockResolvedValueOnce({
      document: makeDocument({ confidentiality_tag: "restricted" }),
      is_superseded: false,
      current_version: null,
      access: { allowed: false, reason: "Elevated permissions are required to view it." },
    });

    render(<SourceVerificationPanel citation={excelCitation} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/access restricted/i)).toBeInTheDocument());
    expect(screen.queryByText("budget.xlsx")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open original file/i })).not.toBeInTheDocument();
  });
});
