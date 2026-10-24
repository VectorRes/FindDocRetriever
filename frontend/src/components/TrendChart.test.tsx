import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TrendChart, { formatRatioValue } from "./TrendChart";
import type { DashboardPoint } from "../api/types";

function point(period: string, value: number | null, extra: Partial<DashboardPoint> = {}): DashboardPoint {
  return {
    period,
    status: value == null ? "not_calculable" : "calculated",
    value,
    currency: null,
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

const OUTLIER = {
  previous_period: "Q1",
  previous_value: 0.12,
  change: 1.83,
  absolute_change: 0.22,
  method: "period_change" as const,
  message: "ROE rose",
  drivers: [],
};

describe("TrendChart", () => {
  it("describes every period, values and gaps, for screen readers", () => {
    render(<TrendChart points={[point("Q1", 0.12), point("Q2", null), point("Q3", 0.15)]} unit="percent" title="ROE" />);
    expect(screen.getByRole("img")).toHaveAccessibleName("ROE: Q1 12.0%, Q2 not calculable, Q3 15.0%");
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  it("marks an outlier with an icon and a label, not color alone", () => {
    render(<TrendChart points={[point("Q1", 0.12), point("Q2", 0.34, { outlier: OUTLIER })]} unit="percent" title="ROE" />);
    expect(screen.getByTestId("outlier-Q2")).toBeInTheDocument();
    expect(screen.getByText("Outlier")).toBeInTheDocument();
    expect(screen.getByText("!")).toBeInTheDocument();
  });

  it("shows a tooltip on hover and selects a period on click", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <TrendChart
        points={[point("Q1", 0.12), point("Q2", 0.34, { outlier: OUTLIER })]}
        unit="percent"
        title="ROE"
        onSelectPeriod={onSelect}
      />
    );
    const bands = container.querySelectorAll("rect");
    fireEvent.mouseEnter(bands[1]);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Q234.0%⚠ Outlier — click to review drivers");
    fireEvent.click(bands[1]);
    expect(onSelect).toHaveBeenCalledWith("Q2");
  });

  it("formats percentages and multiples", () => {
    expect(formatRatioValue(0.25, "percent")).toBe("25.0%");
    expect(formatRatioValue(1.754, "times")).toBe("1.75x");
    expect(formatRatioValue(null, "times")).toBe("—");
  });
});
