import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ComparisonView from "./ComparisonView";
import { compareDocuments, exportComparison, listDocuments } from "../api/client";
import type { Comparison, ComparisonRow, DocumentOut } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, listDocuments: vi.fn(), compareDocuments: vi.fn(), exportComparison: vi.fn() };
});

const mockedList = vi.mocked(listDocuments);
const mockedCompare = vi.mocked(compareDocuments);
const mockedExport = vi.mocked(exportComparison);

function makeDocument(id: string, filename: string, overrides: Partial<DocumentOut> = {}): DocumentOut {
  return {
    id,
    filename,
    doc_type: "excel",
    status: "ready",
    error_message: null,
    warnings: [],
    uploaded_at: "2026-01-01T00:00:00Z",
    is_current: true,
    superseded_by_id: null,
    version_group: `excel:${id}`,
    version_number: 1,
    approval_status: "approved",
    approved_at: null,
    confidentiality_tag: "public",
    sheet_names: [],
    ...overrides,
  };
}

function makeRow(overrides: Partial<ComparisonRow>): ComparisonRow {
  return {
    document_id: "bi",
    filename: "BI_export.xlsx",
    version_number: 1,
    is_current: true,
    is_baseline: false,
    found: true,
    value: 0,
    value_text: "0",
    unit_scale: "units",
    currency: "COP",
    period: "Q1 2025",
    label: "Revenue",
    citation: {
      source_id: "S1",
      document_id: "bi",
      document_filename: "BI_export.xlsx",
      sheet_name: "Resumen",
      cell_range: "A2:B2",
      page_number: null,
      reference_number: null,
      text: "Revenue: 4,820,000",
    },
    confidence: "high",
    reason: null,
    comparable_value: 0,
    converted: false,
    variance_abs: null,
    variance_pct: null,
    matches_baseline: null,
    ...overrides,
  };
}

const discrepancy: Comparison = {
  metric: "Revenue",
  period: "Q1 2025",
  rows: [
    makeRow({ document_id: "bi", filename: "BI_export.xlsx", is_baseline: true, value: 4820000, comparable_value: 4820000 }),
    makeRow({
      document_id: "erp",
      filename: "ERP_report.xlsx",
      value: 4795000,
      comparable_value: 4795000,
      variance_abs: -25000,
      variance_pct: -0.5187,
      matches_baseline: false,
    }),
  ],
  reconciles: false,
  across_periods: false,
  comparison_currency: "COP",
  exchange_rates: [],
  notes: [],
  escalation: { team: "Accounting/Consolidation", topic: "consolidation", reason: "Don't reconcile." },
};

async function selectAndCompare(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByLabelText("Compare ERP_report.xlsx"));
  await user.click(screen.getByLabelText("Compare BI_export.xlsx"));
  await user.type(screen.getByPlaceholderText(/net income/i), "Revenue");
  await user.click(screen.getByRole("button", { name: /^compare$/i }));
}

describe("ComparisonView", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedCompare.mockReset();
    mockedExport.mockReset();
    mockedList.mockResolvedValue({
      documents: [
        makeDocument("bi", "BI_export.xlsx"),
        makeDocument("erp", "ERP_report.xlsx"),
        makeDocument("failed", "broken.xlsx", { status: "failed" }),
      ],
    });
  });

  it("lists only processed documents and marks the first selected as baseline", async () => {
    const user = userEvent.setup();
    render(<ComparisonView onCitationClick={vi.fn()} />);

    await user.click(await screen.findByLabelText("Compare ERP_report.xlsx"));
    expect(screen.queryByText("broken.xlsx")).not.toBeInTheDocument();
    const erpRow = screen.getByLabelText("Compare ERP_report.xlsx").closest("label")!;
    expect(within(erpRow).getByText("baseline")).toBeInTheDocument();
  });

  it("needs two documents and a metric before comparing", async () => {
    const user = userEvent.setup();
    render(<ComparisonView onCitationClick={vi.fn()} />);

    await user.click(await screen.findByLabelText("Compare ERP_report.xlsx"));
    await user.type(screen.getByPlaceholderText(/net income/i), "Revenue");
    expect(screen.getByRole("button", { name: /^compare$/i })).toBeDisabled();
  });

  it("sends the documents in selection order and highlights a discrepancy with the team to consult", async () => {
    const user = userEvent.setup();
    mockedCompare.mockResolvedValueOnce(discrepancy);
    render(<ComparisonView onCitationClick={vi.fn()} />);

    await selectAndCompare(user);

    await waitFor(() =>
      expect(mockedCompare).toHaveBeenCalledWith({ document_ids: ["erp", "bi"], metric: "Revenue", period: null })
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("don't reconcile");
    expect(screen.getByText("Accounting/Consolidation")).toBeInTheDocument();

    const erp = screen.getByText("ERP_report.xlsx", { selector: "div" }).closest("tr")!;
    expect(erp).toHaveAttribute("data-mismatch", "true");
    expect(within(erp).getByText("-25,000")).toBeInTheDocument();
    expect(within(erp).getByText("-0.52%")).toBeInTheDocument();
  });

  it("opens the source panel from a value's citation", async () => {
    const user = userEvent.setup();
    const onCitationClick = vi.fn();
    mockedCompare.mockResolvedValueOnce(discrepancy);
    render(<ComparisonView onCitationClick={onCitationClick} />);

    await selectAndCompare(user);
    const erp = (await screen.findByText("ERP_report.xlsx", { selector: "div" })).closest("tr")!;
    await user.click(within(erp).getByRole("button"));

    expect(onCitationClick).toHaveBeenCalledWith(discrepancy.rows[1].citation);
  });

  it("shows variance without a reconciliation verdict when comparing periods", async () => {
    const user = userEvent.setup();
    mockedCompare.mockResolvedValueOnce({
      ...discrepancy,
      reconciles: null,
      across_periods: true,
      escalation: null,
      rows: [
        { ...discrepancy.rows[0], period: "2024" },
        { ...discrepancy.rows[1], period: "2025", value: 5310000, comparable_value: 5310000, variance_abs: 490000, variance_pct: 10.166, matches_baseline: null },
      ],
    });
    render(<ComparisonView onCitationClick={vi.fn()} />);

    await selectAndCompare(user);

    expect(await screen.findByText(/comparing across periods/i)).toBeInTheDocument();
    expect(screen.getByText("+490,000")).toBeInTheDocument();
    expect(screen.getByText("+10.17%")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a not-found figure without a value, with the reason", async () => {
    const user = userEvent.setup();
    mockedCompare.mockResolvedValueOnce({
      ...discrepancy,
      reconciles: null,
      escalation: null,
      rows: [
        discrepancy.rows[0],
        makeRow({ document_id: "erp", filename: "ERP_report.xlsx", found: false, value: null, comparable_value: null, citation: null, reason: "This document doesn't state Revenue." }),
      ],
    });
    render(<ComparisonView onCitationClick={vi.fn()} />);

    await selectAndCompare(user);

    expect(await screen.findByText("not found")).toBeInTheDocument();
    expect(screen.getByText("This document doesn't state Revenue.")).toBeInTheDocument();
    expect(screen.getByText(/can't be determined/i)).toBeInTheDocument();
  });

  it("exports the comparison on screen to Excel and prints for PDF", async () => {
    const user = userEvent.setup();
    mockedCompare.mockResolvedValueOnce(discrepancy);
    mockedExport.mockResolvedValueOnce(new Blob(["xlsx"]));
    const createObjectURL = vi.fn(() => "blob:comparison");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    const download = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<ComparisonView onCitationClick={vi.fn()} />);

    await selectAndCompare(user);
    await user.click(await screen.findByRole("button", { name: /export to excel/i }));
    await waitFor(() => expect(mockedExport).toHaveBeenCalledWith(discrepancy));
    expect(createObjectURL).toHaveBeenCalled();
    expect(download).toHaveBeenCalled();
    download.mockRestore();

    await user.click(screen.getByRole("button", { name: /save as pdf/i }));
    expect(print).toHaveBeenCalled();
  });
});
