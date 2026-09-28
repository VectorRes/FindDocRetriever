import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DocumentUpload from "./DocumentUpload";
import { uploadDocument } from "../api/client";
import type { DocumentOut } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    uploadDocument: vi.fn(),
  };
});

const mockedUploadDocument = vi.mocked(uploadDocument);

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
    version_group: "excel:budget",
    version_number: 1,
    approval_status: "approved",
    approved_at: null,
    confidentiality_tag: "public",
    sheet_names: ["Sheet1"],
    ...overrides,
  };
}

function getFileInput(container: HTMLElement): HTMLInputElement {
  return container.querySelector('input[type="file"]')!;
}

describe("DocumentUpload", () => {
  beforeEach(() => {
    mockedUploadDocument.mockReset();
  });

  it("uploads a valid file and calls onUploaded", async () => {
    const user = userEvent.setup();
    const onUploaded = vi.fn();
    mockedUploadDocument.mockResolvedValueOnce(makeDocument());

    const { container } = render(<DocumentUpload onUploaded={onUploaded} />);

    const file = new File(["dummy"], "budget.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await user.upload(getFileInput(container), file);

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(makeDocument()));
    expect(mockedUploadDocument).toHaveBeenCalledWith(file);
  });

  it("rejects an unsupported file type without calling the API", async () => {
    const onUploaded = vi.fn();

    const { container } = render(<DocumentUpload onUploaded={onUploaded} />);

    // userEvent.upload enforces the input's `accept` attribute client-side and
    // would silently no-op here, so this bypasses it the way a browser drag-drop
    // (which ignores `accept`) or a spoofed extension would reach our own check.
    const file = new File(["dummy"], "notes.txt", { type: "text/plain" });
    fireEvent.change(getFileInput(container), { target: { files: [file] } });

    await waitFor(() => expect(screen.getByText(/unsupported file type/i)).toBeInTheDocument());
    expect(mockedUploadDocument).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it("shows an error with a retry option when the upload fails", async () => {
    const user = userEvent.setup();
    const onUploaded = vi.fn();
    mockedUploadDocument.mockRejectedValueOnce(new Error("network down"));
    mockedUploadDocument.mockResolvedValueOnce(makeDocument());

    const { container } = render(<DocumentUpload onUploaded={onUploaded} />);

    const file = new File(["dummy"], "budget.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await user.upload(getFileInput(container), file);

    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(makeDocument()));
    expect(mockedUploadDocument).toHaveBeenCalledTimes(2);
  });
});
