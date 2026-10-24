import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DashboardView, { changeText, pointText } from "./DashboardView";
import { ApiError, exportDashboard, getDashboard, getDashboardCatalog, refreshDashboard } from "../api/client";
import type {
  CitationOut,
  Dashboard,
  DashboardPoint,
  DashboardRatio,
  RatioInput,
} from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getDashboardCatalog: vi.fn(),
    getDashboard: vi.fn(),
    refreshDashboard: vi.fn(),
    exportDashboard: vi.fn(),
  };
});

const mockedCatalog = vi.mocked(getDashboardCatalog);
const mockedDashboard = vi.mocked(getDashboard);
const mockedRefresh = vi.mocked(refreshDashboard);
const mockedExport = vi.mocked(exportDashboard);

function citation(filename: string, cell: string, text: string): CitationOut {
  return {
    source_id: "S1",
    document_id: filename,
    document_filename: filename,
    sheet_name: "Hoja1",
    cell_range: cell,
    page_number: null,
    reference_number: null,
    text,
  };
}

function input(key: string, name: string, label: string, value: number, filename: string): RatioInput {
  return {
    key,
    name,
    statement: key === "total_equity" ? "balance_sheet" : "income_statement",
    found: true,
    value,
    value_text: String(value),
    unit_scale: "units",
    currency: "COP",
    period: null,
    label,
    document_id: filename,
    document_filename: filename,
    citation: citation(filename, "A5:B5", `${label}: ${value}`),
    confidence: "high",
    needs_review: false,
    reason: null,
  } as RatioInput;
}

function point(period: string, value: number | null, extra: Partial<DashboardPoint> = {}): DashboardPoint {
  return {
    period,
    status: value == null ? "not_calculable" : "calculated",
    value,
    currency: "COP",
    inputs: [],
    citations: [],
    cross_document: false,
    reasons: [],
    missing_statements: [],
    missing_inputs: [],
    stale: false,
    outlier: null,
    ...extra,
  };
}

function ratio(key: string, name: string, category: DashboardRatio["category"], unit: "percent" | "times", points: DashboardPoint[]): DashboardRatio {
  return { key, name, category, formula: `${name} formula`, unit, description: "", points };
}

const ROE_2025 = point("2025", 0.34, {
  cross_document: true,
  inputs: [
    input("net_income", "Net income", "Utilidad neta", 700400, "EERR_Andina_2025.xlsx"),
    input("total_equity", "Total equity", "Total patrimonio", 2060000, "Balance_Andina_2025.xlsx"),
  ],
  outlier: {
    previous_period: "2024",
    previous_value: 0.13,
    change: 1.6,
    absolute_change: 0.21,
    method: "period_change",
    message: "Return on equity (ROE) rose from 13.0% in 2024 to 34.0% in 2025; the main driver is equity multiplier.",
    drivers: [
      { name: "Net margin", previous: 0.12, current: 0.121, unit: "percent", change: 0.01, note: "profitability" },
      { name: "Equity multiplier", previous: 1.66, current: 4.17, unit: "times", change: 1.51, note: "leverage" },
    ],
  },
});

function andina(): Dashboard {
  return {
    entity: "Andina",
    entity_key: "andina",
    notes: [],
    periods: ["2024", "2025"].map((period) => ({
      period,
      calculated: true,
      gap: false,
      stale: false,
      restricted: false,
      computed_at: "2026-10-20T12:00:00Z",
      computed_by: "ana",
      documents: [],
      notes: [],
    })),
    ratios: [
      ratio("roe", "Return on equity (ROE)", "profitability", "percent", [point("2024", 0.13), ROE_2025]),
      ratio("roa", "Return on assets (ROA)", "profitability", "percent", [point("2024", 0.078), point("2025", 0.081)]),
      ratio("ebitda_margin", "EBITDA margin", "profitability", "percent", [point("2024", 0.278), point("2025", 0.276)]),
      ratio("current_ratio", "Current ratio", "liquidity", "times", [point("2024", 2.0), point("2025", 1.45)]),
      ratio("debt_ratio", "Debt ratio", "solvency", "percent", [
        point("2024", 0.398),
        point("2025", null, {
          missing_statements: ["balance_sheet"],
          missing_inputs: ["Total liabilities", "Total assets"],
          reasons: ["Insufficient data: the balance sheet for 2025 hasn't been ingested for Andina."],
        }),
      ]),
    ],
  };
}

const CATALOG = {
  entities: [
    { entity: "Andina", key: "andina", periods: ["2024", "2025"], documents: [] },
    { entity: "Pacifico", key: "pacifico", periods: ["2024"], documents: [] },
  ],
};

describe("DashboardView", () => {
  beforeEach(() => {
    mockedCatalog.mockReset().mockResolvedValue(CATALOG);
    mockedDashboard.mockReset().mockResolvedValue(andina());
    mockedRefresh.mockReset();
    mockedExport.mockReset();
  });

  it("shows the key ratios for the latest period with the change against the previous one", async () => {
    render(<DashboardView onCitationClick={() => {}} />);

    expect(await screen.findByTestId("tile-value-roe")).toHaveTextContent("34.0%");
    expect(screen.getByTestId("tile-value-current_ratio")).toHaveTextContent("1.45x");
    expect(screen.getByRole("button", { name: "Return on equity (ROE) tile" })).toHaveTextContent("+21.0 pp vs 2024");
    expect(mockedDashboard).toHaveBeenCalledWith("Andina");
  });

  it("cites every contributing document of a ratio, grouped by document", async () => {
    const onCitationClick = vi.fn();
    render(<DashboardView onCitationClick={onCitationClick} />);

    const detail = await screen.findByRole("region", { name: "Return on equity (ROE) detail" });
    expect(within(detail).getByText("cross-document: 2 sources")).toBeInTheDocument();
    const fromIncome = within(detail).getByRole("table", { name: "Inputs from EERR_Andina_2025.xlsx" });
    expect(within(fromIncome).getByText("Utilidad neta")).toBeInTheDocument();
    within(detail).getByRole("table", { name: "Inputs from Balance_Andina_2025.xlsx" });

    await userEvent.click(within(fromIncome).getByRole("button"));
    expect(onCitationClick).toHaveBeenCalledWith(expect.objectContaining({ document_filename: "EERR_Andina_2025.xlsx" }));
  });

  it("flags the outlier and lists its drivers for review", async () => {
    render(<DashboardView onCitationClick={() => {}} />);

    const detail = await screen.findByRole("region", { name: "Return on equity (ROE) detail" });
    expect(within(detail).getByTestId("outlier-2025")).toBeInTheDocument();
    const review = within(detail).getByRole("alert");
    expect(review).toHaveTextContent("Outlier in 2025 — review before presenting");
    const drivers = within(review).getByRole("table", { name: "Return on equity (ROE) drivers 2025" });
    expect(within(drivers).getByText("Equity multiplier")).toBeInTheDocument();
    expect(within(drivers).getByText("4.17x")).toBeInTheDocument();
  });

  it("says which document is missing instead of showing a blank or zero", async () => {
    render(<DashboardView onCitationClick={() => {}} />);

    await userEvent.click(await screen.findByRole("button", { name: "Debt ratio card" }));

    expect(screen.getByTestId("card-value-debt_ratio")).toHaveTextContent("Not calculable");
    const detail = screen.getByRole("region", { name: "Debt ratio detail" });
    expect(within(detail).getByRole("status")).toHaveTextContent("Missing document: the balance sheet for 2025");
    expect(within(detail).getByTestId("missing-inputs")).toHaveTextContent("Missing inputs: Total liabilities, Total assets.");
  });

  it("names the missing line item of a ratio that can't be calculated (ID-HU-BE-011)", async () => {
    const dashboard = andina();
    dashboard.ratios.push(
      ratio("cash_ratio", "Cash ratio", "liquidity", "times", [
        point("2024", 0.3),
        point("2025", null, {
          missing_inputs: ["Current liabilities"],
          reasons: ["Missing input: Current liabilities."],
        }),
      ]),
    );
    mockedDashboard.mockResolvedValue(dashboard);
    render(<DashboardView onCitationClick={() => {}} />);

    await userEvent.click(await screen.findByRole("button", { name: "Cash ratio card" }));

    expect(screen.getByTestId("card-value-cash_ratio")).toHaveTextContent("Not calculable");
    expect(screen.getByRole("button", { name: "Cash ratio card" })).toHaveTextContent("missing current liabilities");
    const detail = screen.getByRole("region", { name: "Cash ratio detail" });
    expect(within(detail).getByTestId("missing-inputs")).toHaveTextContent("Missing input: Current liabilities.");
    // Said once, not again in the list of reasons.
    expect(within(detail).getAllByText("Missing input: Current liabilities.")).toHaveLength(1);
  });

  it("shows a prior period when it's selected", async () => {
    render(<DashboardView onCitationClick={() => {}} />);
    await screen.findByTestId("tile-value-roe");

    await userEvent.selectOptions(screen.getByLabelText("Period shown"), "2024");

    expect(screen.getByTestId("tile-value-roe")).toHaveTextContent("13.0%");
    expect(screen.getByTestId("tile-value-current_ratio")).toHaveTextContent("2.00x");
  });

  it("filters by category and period", async () => {
    render(<DashboardView onCitationClick={() => {}} />);
    await screen.findByTestId("tile-value-roe");

    await userEvent.click(screen.getByLabelText("Liquidity"));
    expect(screen.queryByRole("region", { name: "Liquidity ratios" })).not.toBeInTheDocument();

    await userEvent.click(within(screen.getByRole("group", { name: /Periods/ })).getByLabelText("2024"));
    expect(screen.getByLabelText("Period shown")).toHaveValue("2024");
    expect(screen.queryByRole("option", { name: "2025" })).not.toBeInTheDocument();
  });

  it("recalculates the chosen periods and switches entity", async () => {
    mockedRefresh.mockResolvedValue(andina());
    render(<DashboardView onCitationClick={() => {}} />);
    await screen.findByTestId("tile-value-roe");

    await userEvent.click(within(screen.getByRole("group", { name: /Periods/ })).getByLabelText("2025"));
    await userEvent.type(screen.getByLabelText("Add a period"), "2022");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await userEvent.click(screen.getByRole("button", { name: "Calculate 2025, 2022" }));
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalledWith("Andina", ["2025", "2022"]));

    mockedDashboard.mockResolvedValue({ ...andina(), entity: "Pacifico", entity_key: "pacifico" });
    await userEvent.selectOptions(screen.getByLabelText("Entity"), "Pacifico");
    await waitFor(() => expect(mockedDashboard).toHaveBeenLastCalledWith("Pacifico"));
  });

  it("shows the API's message when the model service is down", async () => {
    mockedRefresh.mockRejectedValue(new ApiError("The language model service didn't respond.", "http", 503));
    render(<DashboardView onCitationClick={() => {}} />);
    await screen.findByTestId("tile-value-roe");

    await userEvent.click(screen.getByRole("button", { name: "Calculate all periods" }));

    expect(await screen.findByText("The language model service didn't respond.")).toBeInTheDocument();
  });

  it("exports the dashboard on screen to Excel", async () => {
    mockedExport.mockResolvedValue(new Blob(["x"]));
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<DashboardView onCitationClick={() => {}} />);
    await screen.findByTestId("tile-value-roe");

    await userEvent.click(screen.getByLabelText("Liquidity"));
    await userEvent.click(screen.getByRole("button", { name: "Export to Excel" }));

    const exported = mockedExport.mock.calls[0][0];
    expect(exported.ratios.map((r) => r.key)).not.toContain("current_ratio");
    expect(click).toHaveBeenCalled();
    click.mockRestore();
  });
});

describe("pointText and changeText", () => {
  it("never shows a held or missing value as a number", () => {
    expect(pointText(point("2025", 0.25, { status: "needs_review" }), "percent")).toBe("Held for review");
    expect(pointText(point("2025", 0.25, { status: "needs_review" }), "percent", true)).toBe("25.0%");
    expect(pointText(point("2025", null), "percent")).toBe("Not calculable");
  });

  it("compares with the previous calculated period", () => {
    const r = ratio("current_ratio", "Current ratio", "liquidity", "times", [
      point("2023", 2.0),
      point("2024", null),
      point("2025", 1.45),
    ]);
    expect(changeText(r, "2025")).toBe("−0.55x vs 2023");
    expect(changeText(r, "2023")).toBeNull();
  });
});
