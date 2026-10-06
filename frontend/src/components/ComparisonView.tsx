import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { ApiError, compareDocuments, exportComparison, listDocuments } from "../api/client";
import type { CitationOut, Comparison, ComparisonRow, DocumentOut } from "../api/types";
import CitationBadge from "./CitationBadge";

// ID-HU-FE-003: compare one metric across documents or periods side by side.

const METRIC_SUGGESTIONS = [
  "Revenue",
  "Net income",
  "Gross profit",
  "EBITDA",
  "Operating cash flow",
  "Total assets",
  "Total liabilities",
  "Current liabilities",
  "Equity",
];

const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

function formatNumber(value: number | null, currency?: string | null): string {
  if (value == null) return "—";
  return `${numberFormat.format(value)}${currency ? ` ${currency}` : ""}`;
}

function formatVariance(row: ComparisonRow): { abs: string; pct: string } {
  if (row.is_baseline) return { abs: "baseline", pct: "" };
  if (row.variance_abs == null) return { abs: "—", pct: "—" };
  const sign = row.variance_abs > 0 ? "+" : "";
  const pct = row.variance_pct == null ? "—" : `${row.variance_pct > 0 ? "+" : ""}${row.variance_pct.toFixed(2)}%`;
  return { abs: `${sign}${numberFormat.format(row.variance_abs)}`, pct };
}

interface ComparisonViewProps {
  onCitationClick: (citation: CitationOut) => void;
  // Reload the document list whenever the view is shown (new uploads, approvals).
  active?: boolean;
}

export default function ComparisonView({ onCitationClick, active = true }: ComparisonViewProps) {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [metric, setMetric] = useState("");
  const [period, setPeriod] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<Comparison | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (!active) return;
    listDocuments()
      .then((list) => {
        const ready = list.documents.filter((doc) => doc.status === "ready");
        setDocuments(ready);
        // Drop selections of documents that were deleted meanwhile.
        setSelected((prev) => prev.filter((id) => ready.some((doc) => doc.id === id)));
      })
      .catch(() => setErrorMessage("Could not load the document list."));
  }, [active]);

  function toggle(documentId: string) {
    setSelected((prev) =>
      prev.includes(documentId) ? prev.filter((id) => id !== documentId) : [...prev, documentId]
    );
  }

  async function runComparison() {
    setStatus("loading");
    setErrorMessage(null);
    try {
      const comparison = await compareDocuments({
        document_ids: selected,
        metric: metric.trim(),
        period: period.trim() || null,
      });
      setResult(comparison);
      setStatus("idle");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "The comparison failed.");
      setStatus("error");
    }
  }

  async function downloadExcel() {
    if (!result) return;
    setExporting(true);
    try {
      const blob = await exportComparison(result);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `comparison-${result.metric.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      setErrorMessage("Could not export the comparison to Excel.");
    } finally {
      setExporting(false);
    }
  }

  const canCompare = selected.length >= 2 && selected.length <= 6 && metric.trim().length > 0 && status !== "loading";

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1rem" }}>
      <section className="no-print" style={panelStyle} aria-label="Comparison setup">
        <div>
          <h2 style={sectionTitleStyle}>Documents to compare</h2>
          <p style={hintStyle}>
            Select 2 to 6 documents. The first one you select is the baseline that variances are measured against.
          </p>
          {documents.length === 0 && <p style={hintStyle}>No processed documents yet. Upload some from Documents.</p>}
          <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem", maxHeight: "14rem", overflowY: "auto" }}>
            {documents.map((doc) => {
              const position = selected.indexOf(doc.id);
              return (
                <label key={doc.id} style={documentOptionStyle}>
                  <input
                    type="checkbox"
                    checked={position >= 0}
                    onChange={() => toggle(doc.id)}
                    aria-label={`Compare ${doc.filename}`}
                  />
                  <span style={{ flex: 1, wordBreak: "break-all" }}>{doc.filename}</span>
                  <span style={chipStyle}>v{doc.version_number}</span>
                  {!doc.is_current && <span style={{ ...chipStyle, color: "#64748b" }}>not current</span>}
                  {position === 0 && <span style={{ ...chipStyle, ...baselineChipStyle }}>baseline</span>}
                  {position > 0 && <span style={chipStyle}>#{position + 1}</span>}
                </label>
              );
            })}
          </div>
        </div>

        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={fieldStyle}>
            Metric or line item
            <input
              list="metric-suggestions"
              value={metric}
              onChange={(e) => setMetric(e.target.value)}
              placeholder="e.g. Net income"
              style={inputStyle}
            />
            <datalist id="metric-suggestions">
              {METRIC_SUGGESTIONS.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </label>
          <label style={fieldStyle}>
            Period (optional)
            <input
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              placeholder="e.g. 2024 or Q1 2025"
              style={inputStyle}
            />
          </label>
          <button type="button" onClick={runComparison} disabled={!canCompare} style={primaryButtonStyle(canCompare)}>
            {status === "loading" ? "Comparing..." : "Compare"}
          </button>
        </div>
        {status === "loading" && (
          <p style={hintStyle}>Extracting the figure from each document and checking it against its source…</p>
        )}
        {errorMessage && <p style={{ color: "#b91c1c", fontSize: "0.85rem", margin: 0 }}>{errorMessage}</p>}
      </section>

      {result && (
        <section aria-label="Comparison result" style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
            <h2 style={{ ...sectionTitleStyle, fontSize: "1.1rem" }}>
              {result.metric}
              {result.period ? ` · ${result.period}` : ""}
            </h2>
            <div className="no-print" style={{ display: "flex", gap: "0.5rem" }}>
              <button type="button" onClick={downloadExcel} disabled={exporting} style={secondaryButtonStyle}>
                {exporting ? "Exporting..." : "Export to Excel"}
              </button>
              <button type="button" onClick={() => window.print()} style={secondaryButtonStyle}>
                Save as PDF
              </button>
            </div>
          </div>

          <ReconciliationBanner comparison={result} />

          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: "0.5rem" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  {["Document", "Period", "Line item", "Value", `Compared (${result.comparison_currency ?? "—"})`, "Variance", "Variance %", "Source"].map((h) => (
                    <th key={h} style={thStyle}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row) => {
                  const variance = formatVariance(row);
                  const mismatch = row.matches_baseline === false;
                  return (
                    <tr key={row.document_id} style={mismatch ? mismatchRowStyle : undefined} data-mismatch={mismatch || undefined}>
                      <td style={tdStyle}>
                        <div style={{ fontWeight: 600, wordBreak: "break-all" }}>{row.filename}</div>
                        <div style={{ fontSize: "0.75rem", color: "#64748b" }}>
                          v{row.version_number}
                          {!row.is_current && " · not current"}
                          {row.is_baseline && " · baseline"}
                        </div>
                      </td>
                      <td style={tdStyle}>{row.period ?? "—"}</td>
                      <td style={tdStyle}>
                        {row.label ?? "—"}
                        {row.found && row.confidence !== "high" && (
                          <div style={{ fontSize: "0.75rem", color: "#b45309" }}>{row.confidence} confidence</div>
                        )}
                      </td>
                      <td style={{ ...tdStyle, ...numberCellStyle, color: mismatch ? "#b91c1c" : undefined, fontWeight: mismatch ? 700 : undefined }}>
                        {row.found ? formatNumber(row.value, row.currency) : <span style={{ color: "#64748b" }}>not found</span>}
                      </td>
                      <td style={{ ...tdStyle, ...numberCellStyle }}>
                        {row.found ? formatNumber(row.comparable_value) : "—"}
                        {row.converted && <div style={{ fontSize: "0.72rem", color: "#64748b" }}>converted</div>}
                      </td>
                      <td style={{ ...tdStyle, ...numberCellStyle }}>{variance.abs}</td>
                      <td style={{ ...tdStyle, ...numberCellStyle }}>{variance.pct}</td>
                      <td style={tdStyle}>
                        {row.citation ? (
                          <CitationBadge citation={row.citation} onClick={onCitationClick} />
                        ) : (
                          <span style={{ fontSize: "0.78rem", color: "#64748b" }}>{row.reason ?? "—"}</span>
                        )}
                        {row.citation && row.reason && (
                          <div style={{ fontSize: "0.72rem", color: "#b45309", marginTop: "0.2rem" }}>{row.reason}</div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {result.exchange_rates.map((rate) => (
            <div key={`${rate.from_currency}-${rate.to_currency}`} style={{ fontSize: "0.85rem", color: "#334155" }}>
              Exchange rate used: 1 {rate.from_currency} = {numberFormat.format(rate.rate)} {rate.to_currency} (as
              documented: {rate.rate_text})
              <CitationBadge citation={rate.citation} onClick={onCitationClick} />
            </div>
          ))}

          {result.notes.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "0.82rem", color: "#475569" }}>
              {result.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

function ReconciliationBanner({ comparison }: { comparison: Comparison }) {
  if (comparison.across_periods) {
    return (
      <div style={bannerStyle("#eff6ff", "#1d4ed8")}>
        Comparing across periods: the variance against the baseline is shown in amount and percentage.
      </div>
    );
  }
  if (comparison.reconciles === true) {
    return <div style={bannerStyle("#f0fdf4", "#15803d")}>The values reconcile across the selected documents.</div>;
  }
  if (comparison.reconciles === false) {
    return (
      <div style={bannerStyle("#fef2f2", "#b91c1c")} role="alert">
        <strong>The values don't reconcile.</strong> Rows that differ from the baseline are highlighted in red.
        {comparison.escalation && (
          <div style={{ marginTop: "0.3rem" }}>
            Suggested team to reconcile with: <strong>{comparison.escalation.team}</strong>
          </div>
        )}
      </div>
    );
  }
  return (
    <div style={bannerStyle("#f8fafc", "#475569")}>
      Reconciliation can't be determined: fewer than two comparable values, or a currency without a documented
      exchange rate.
    </div>
  );
}

const panelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  padding: "1rem",
  border: "1px solid #e2e8f0",
  borderRadius: "0.6rem",
  background: "white",
};
const sectionTitleStyle: CSSProperties = { fontSize: "0.95rem", margin: "0 0 0.25rem" };
const hintStyle: CSSProperties = { fontSize: "0.8rem", color: "#64748b", margin: "0 0 0.5rem" };
const documentOptionStyle: CSSProperties = {
  display: "flex",
  gap: "0.5rem",
  alignItems: "center",
  padding: "0.35rem 0.5rem",
  border: "1px solid #e2e8f0",
  borderRadius: "0.4rem",
  fontSize: "0.85rem",
  cursor: "pointer",
};
const chipStyle: CSSProperties = {
  fontSize: "0.7rem",
  padding: "0.05rem 0.45rem",
  borderRadius: "999px",
  border: "1px solid #e2e8f0",
  whiteSpace: "nowrap",
};
const baselineChipStyle: CSSProperties = { background: "#eef2ff", color: "#4338ca", borderColor: "#c7d2fe" };
const fieldStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.8rem", color: "#334155" };
const inputStyle: CSSProperties = { padding: "0.4rem 0.55rem", border: "1px solid #cbd5e1", borderRadius: "0.4rem", fontSize: "0.9rem", minWidth: "12rem" };
const secondaryButtonStyle: CSSProperties = {
  padding: "0.35rem 0.75rem",
  borderRadius: "0.4rem",
  border: "1px solid #4f46e5",
  background: "white",
  color: "#4f46e5",
  cursor: "pointer",
  fontSize: "0.82rem",
};
function primaryButtonStyle(enabled: boolean): CSSProperties {
  return {
    padding: "0.45rem 1rem",
    borderRadius: "0.4rem",
    border: "none",
    background: enabled ? "#4f46e5" : "#a5b4fc",
    color: "white",
    cursor: enabled ? "pointer" : "not-allowed",
    fontSize: "0.9rem",
  };
}
const tableStyle: CSSProperties = { borderCollapse: "collapse", width: "100%", minWidth: "48rem", fontSize: "0.85rem" };
const thStyle: CSSProperties = {
  textAlign: "left",
  padding: "0.5rem 0.6rem",
  background: "#f8fafc",
  borderBottom: "1px solid #e2e8f0",
  fontSize: "0.75rem",
  color: "#475569",
  whiteSpace: "nowrap",
};
const tdStyle: CSSProperties = { padding: "0.5rem 0.6rem", borderBottom: "1px solid #f1f5f9", verticalAlign: "top" };
const numberCellStyle: CSSProperties = { textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" };
const mismatchRowStyle: CSSProperties = { background: "#fef2f2" };

function bannerStyle(background: string, color: string): CSSProperties {
  return { background, color, padding: "0.6rem 0.8rem", borderRadius: "0.5rem", fontSize: "0.88rem" };
}
