import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DocumentList from "./DocumentList";
import { deleteDocument, listDocuments } from "../api/client";
import type { DocumentOut } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    listDocuments: vi.fn(),
    deleteDocument: vi.fn(),
  };
});

const mockedListDocuments = vi.mocked(listDocuments);
const mockedDeleteDocument = vi.mocked(deleteDocument);

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
    sheet_names: ["Assumptions", "FCF"],
    ...overrides,
  };
}

describe("DocumentList", () => {
  beforeEach(() => {
    mockedListDocuments.mockReset();
    mockedDeleteDocument.mockReset();
  });

  it("renders nothing when closed", () => {
    render(<DocumentList open={false} onClose={vi.fn()} />);
    expect(screen.queryByText("Documents")).not.toBeInTheDocument();
    expect(mockedListDocuments).not.toHaveBeenCalled();
  });

  it("shows detected sheets and warnings for a ready Excel document when opened", async () => {
    mockedListDocuments.mockResolvedValueOnce({
      documents: [makeDocument({ warnings: ["Unresolved formula in B7"] })],
    });

    render(<DocumentList open={true} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("budget.xlsx")).toBeInTheDocument());
    expect(screen.getByText(/Assumptions, FCF/)).toBeInTheDocument();
    expect(screen.getByText(/1 parsing warning/i)).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("links a superseded document to the version that replaced it", async () => {
    const current = makeDocument({ id: "doc-2", filename: "budget_v2.xlsx" });
    const old = makeDocument({
      id: "doc-1",
      filename: "budget.xlsx",
      is_current: false,
      superseded_by_id: "doc-2",
    });
    mockedListDocuments.mockResolvedValueOnce({ documents: [old, current] });

    render(<DocumentList open={true} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("superseded")).toBeInTheDocument());
    expect(screen.getByText(/superseded by/i)).toBeInTheDocument();
    expect(screen.getByText("budget_v2.xlsx", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("current version")).toBeInTheDocument();
  });

  it("shows a failed document's error message", async () => {
    mockedListDocuments.mockResolvedValueOnce({
      documents: [
        makeDocument({
          status: "failed",
          error_message: "Could not read workbook: corrupted file",
          sheet_names: [],
        }),
      ],
    });

    render(<DocumentList open={true} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("failed")).toBeInTheDocument());
    expect(screen.getByText(/corrupted file/i)).toBeInTheDocument();
  });

  it("shows an empty state when there are no documents", async () => {
    mockedListDocuments.mockResolvedValueOnce({ documents: [] });

    render(<DocumentList open={true} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/no documents uploaded yet/i)).toBeInTheDocument());
  });

  it("deletes a document after confirming, then refreshes the list", async () => {
    const user = userEvent.setup();
    mockedListDocuments.mockResolvedValueOnce({ documents: [makeDocument()] });
    mockedDeleteDocument.mockResolvedValueOnce(undefined);
    mockedListDocuments.mockResolvedValueOnce({ documents: [] });

    render(<DocumentList open={true} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("budget.xlsx")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(screen.getByText(/delete this document\?/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /yes, delete/i }));

    await waitFor(() => expect(mockedDeleteDocument).toHaveBeenCalledWith("doc-1"));
    await waitFor(() => expect(screen.getByText(/no documents uploaded yet/i)).toBeInTheDocument());
  });

  it("cancels a pending delete without calling the API", async () => {
    const user = userEvent.setup();
    mockedListDocuments.mockResolvedValueOnce({ documents: [makeDocument()] });

    render(<DocumentList open={true} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("budget.xlsx")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /^delete$/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByText(/delete this document\?/i)).not.toBeInTheDocument();
    expect(mockedDeleteDocument).not.toHaveBeenCalled();
  });
});
