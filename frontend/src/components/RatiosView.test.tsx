import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RatiosView, { columnNote, formatInputValue, formatRatio } from "./RatiosView";
import { calculateRatios, listDocuments } from "../api/client";
import type { CitationOut, DocumentOut, Ratio, RatioInput, RatioReport } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, listDocuments: vi.fn(), calculateRatios: vi.fn() };
});

const mockedList = vi.mocked(listDocuments);
const mockedCalculate = vi.mocked(calculateRatios);

function makeDocument(id: string, filename: string): DocumentOut {
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
  };
}

function citation(documentId: string, filename: string, cell: string, text: string): CitationOut {
  return {
    source_id: "S1",
    document_id: documentId,
    document_filename: filename,
    sheet_name: "Sheet1",
    cell_range: cell,
    page_number: null,
    reference_number: null,
    text,
  };
}

function input(overrides: Partial<RatioInput>): RatioInput {
  return {
    key: "revenue",
    name: "Revenue",
    statement: "income_statement",
    found: true,
    value: 4820000,
    value_text: "4,820,000",
    unit_scale: "units",
    currency: "COP",
    period: "FY2024",
    label: "Revenue",
    document_id: "is",
    document_filename: "EERR_2024.xlsx",
    citation: citation("is", "EERR_2024.xlsx", "B2", "Revenue: 4,820,000"),
    confidence: "high",
    needs_review: false,
    reason: null,
    ...overrides,
  };
}

function ratio(overrides: Partial<Ratio>): Ratio {
  const inputs = overrides.inputs ?? [];
  return {
    key: "ebitda_margin",
    name: "EBITDA margin",
    category: "profitability",
    formula: "EBITDA / Revenue",
    unit: "percent",
    description: "",
    status: "calculated",
    value: 0.25,
    inputs,
    citations: inputs.flatMap((i) => (i.citation ? [i.citation] : [])),
    cross_document: false,
    currency: "COP",
    period: "FY2024",
    reasons: [],
    ...overrides,
  };
}

const ebitda = input({
  key: "ebitda",
  name: "EBITDA",
  value: 1205000,
  value_text: "1,205,000",
  label: "EBITDA",
  citation: citation("is", "EERR_2024.xlsx", "B5", "EBITDA: 1,205,000"),
});
const revenue = input({});
const netIncome = input({
  key: "net_income",
  name: "Net income",
  value: 610000,
  label: "Net income",
  citation: citation("is", "EERR_2024.xlsx", "B8", "Net income: 610,000"),
});
const totalAssets = input({
  key: "total_assets",
  name: "Total assets",
  statement: "balance_sheet",
  value: 6100000,
  label: "Total assets",
  document_id: "bs",
  document_filename: "Balance_2024.xlsx",
  citation: citation("bs", "Balance_2024.xlsx", "B12", "Total assets: 6,100,000"),
});
const shortTerm = input({
  key: "current_liabilities",
  name: "Current liabilities",
  statement: "balance_sheet",
  value: 1200000,
  label: "Short-Term Obligations",
  document_id: "bs",
  document_filename: "Balance_2024.xlsx",
  citation: citation("bs", "Balance_2024.xlsx", "B9", "Short-Term Obligations: 1,200,000"),
  confidence: "low",
  needs_review: true,
});
const currentAssets = input({
  key: "current_assets",
  name: "Current assets",
  statement: "balance_sheet",
  value: 2100000,
  label: "Total current assets",
  document_id: "bs",
  document_filename: "Balance_2024.xlsx",
  citation: citation("bs", "Balance_2024.xlsx", "B6", "Total current assets: 2,100,000"),
});

const report: RatioReport = {
  period: null,
  document_ids: ["is", "bs"],
  line_items: [],
  notes: [],
  ratios: [
    ratio({
      key: "current_ratio",
      name: "Current ratio",
      category: "liquidity",
      formula: "Current assets / Current liabilities",
      unit: "times",
      status: "needs_review",
      value: 1.75,
      inputs: [currentAssets, shortTerm],
      reasons: ["Current liabilities was mapped to 'Short-Term Obligations' — low-confidence mapping, review it."],
    }),
    ratio({ inputs: [ebitda, revenue] }),
    ratio({
      key: "roa",
      name: "Return on assets (ROA)",
      formula: "Net income / Total assets",
      value: 0.1,
      cross_document: true,
      inputs: [netIncome, totalAssets],
    }),
    ratio({
      key: "inventory_turnover",
      name: "Inventory turnover",
      category: "efficiency",
      unit: "times",
      status: "unavailable",
      value: null,
      inputs: [input({ key: "inventory", name: "Inventory", found: false, value: null, citation: null, label: null })],
      reasons: ["Missing input: Inventory."],
    }),
  ],
};

async function calculate() {
  const user = userEvent.setup();
  render(<RatiosView onCitationClick={vi.fn()} />);
  await user.click(await screen.findByLabelText("Use EERR_2024.xlsx"));
  await user.click(screen.getByLabelText("Use Balance_2024.xlsx"));
  await user.click(screen.getByRole("button", { name: "Calculate ratios" }));
  await screen.findByRole("region", { name: "Ratios" });
  return user;
}

describe("RatiosView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedList.mockResolvedValue({
      documents: [makeDocument("is", "EERR_2024.xlsx"), makeDocument("bs", "Balance_2024.xlsx")],
    });
    mockedCalculate.mockResolvedValue(report);
  });

  it("formats percentages and multiples", () => {
    expect(formatRatio(ratio({ value: 0.25 }))).toBe("25.0%");
    expect(formatRatio(ratio({ unit: "times", value: 1.75 }))).toBe("1.75x");
    expect(formatRatio(ratio({ value: null }))).toBe("—");
  });

  it("adds thousands separators to unformatted cells only", () => {
    expect(formatInputValue(input({ value: 2100000, value_text: "2100000" }))).toBe("2,100,000 COP");
    expect(formatInputValue(input({ value: -4100, value_text: "(4,100)" }))).toBe("(4,100) COP");
  });

  it("shows the column only when it adds something to the label", () => {
    expect(columnNote(input({ label: "Total activos corrientes", column_header: "Total activos corrientes" }))).toBeNull();
    expect(columnNote(input({ label: "Short-Term Obligations", column_header: "Sheet 'Balance general', Short-Term Obligations" }))).toBeNull();
    expect(columnNote(input({ label: "Total net sales", column_header: "Total net sales" }))).toBeNull();
    expect(columnNote(input({ label: "Net income", column_header: "Three Months Ended June 27, 2026" }))).toBe(
      "Three Months Ended June 27, 2026"
    );
    expect(columnNote(input({ label: "Utilidad neta", column_header: "2024" }))).toBe("2024");
  });

  it("shows each input as written in its source, with its scale", () => {
    expect(formatInputValue(input({}))).toBe("4,820,000 COP");
    expect(formatInputValue(input({ value: 149818e6, value_text: "149,818", unit_scale: "millions", currency: "USD" }))).toBe(
      "149,818 USD (in millions)"
    );
  });

  it("sends the selected statements and categories", async () => {
    await calculate();
    expect(mockedCalculate).toHaveBeenCalledWith({
      document_ids: ["is", "bs"],
      period: null,
      categories: ["liquidity", "solvency", "profitability", "efficiency"],
    });
  });

  it("shows EBITDA margin with citations to the revenue and EBITDA line items", async () => {
    await calculate();
    const card = screen.getByRole("article", { name: "EBITDA margin" });
    expect(within(card).getByTestId("ratio-value-ebitda_margin")).toHaveTextContent("25.0%");
    expect(within(card).getByRole("button", { name: /B5/ })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /B2/ })).toBeInTheDocument();
  });

  it("marks cross-document ratios and cites both documents", async () => {
    await calculate();
    const card = screen.getByRole("article", { name: "Return on assets (ROA)" });
    expect(within(card).getByText("cross-document")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /EERR_2024\.xlsx/ })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Balance_2024\.xlsx/ })).toBeInTheDocument();
  });

  it("holds a ratio with a low-confidence mapping until the analyst reviews it", async () => {
    const user = await calculate();
    const card = screen.getByRole("article", { name: "Current ratio" });
    expect(within(card).queryByTestId("ratio-value-current_ratio")).not.toBeInTheDocument();
    expect(within(card).getByText("held for review")).toBeInTheDocument();
    expect(within(card).getByText("low-confidence mapping")).toBeInTheDocument();
    expect(within(card).getByText("Short-Term Obligations")).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: /reviewed it/ }));
    expect(within(card).getByTestId("ratio-value-current_ratio")).toHaveTextContent("1.75x");
  });

  it("explains why a ratio can't be calculated", async () => {
    await calculate();
    const card = screen.getByRole("article", { name: "Inventory turnover" });
    expect(within(card).getByText("can't be calculated")).toBeInTheDocument();
    expect(within(card).getByText("Missing input: Inventory.")).toBeInTheDocument();
  });

  it("opens the source panel from an input's citation", async () => {
    const onCitationClick = vi.fn();
    const user = userEvent.setup();
    render(<RatiosView onCitationClick={onCitationClick} />);
    await user.click(await screen.findByLabelText("Use EERR_2024.xlsx"));
    await user.click(screen.getByRole("button", { name: "Calculate ratios" }));
    const card = await screen.findByRole("article", { name: "EBITDA margin" });
    await user.click(within(card).getByRole("button", { name: /B5/ }));
    expect(onCitationClick).toHaveBeenCalledWith(ebitda.citation);
  });

  it("needs at least one statement and one category", async () => {
    const user = userEvent.setup();
    render(<RatiosView onCitationClick={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Calculate ratios" });
    expect(button).toBeDisabled();
    await user.click(await screen.findByLabelText("Use EERR_2024.xlsx"));
    expect(button).toBeEnabled();
    for (const name of ["Liquidity", "Solvency", "Profitability", "Efficiency"]) {
      await user.click(screen.getByLabelText(name));
    }
    expect(button).toBeDisabled();
    await waitFor(() => expect(mockedCalculate).not.toHaveBeenCalled());
  });
});
