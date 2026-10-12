import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { ApiError, calculateRatios, listDocuments } from "../api/client";
import type { CitationOut, DocumentOut, Ratio, RatioCategory, RatioInput, RatioReport } from "../api/types";
import CitationBadge from "./CitationBadge";

// ID-HU-BE-010: standard financial ratios calculated from ingested statements,
// each with citations to the line items it used.

const CATEGORIES: { key: RatioCategory; label: string }[] = [
  { key: "liquidity", label: "Liquidity" },
  { key: "solvency", label: "Solvency" },
  { key: "profitability", label: "Profitability" },
  { key: "efficiency", label: "Efficiency" },
];

const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function formatRatio(ratio: Ratio): string {
  if (ratio.value == null) return "—";
  if (ratio.unit === "percent") return `${(ratio.value * 100).toFixed(1)}%`;
  return `${ratio.value.toFixed(2)}x`;
}

// As written in the cited source, so it matches what the source panel shows.
export function formatInputValue(input: RatioInput): string {
  if (input.value == null) return "—";
  let written = input.value_text ?? numberFormat.format(input.value);
  // An unformatted cell ("2100000") gets thousands separators; anything
  // already formatted ("149,818", "(4,100)") stays exactly as in the source.
  if (/^-?\d+(\.\d+)?$/.test(written.trim())) written = numberFormat.format(Number(written));
  const scale = input.unit_scale !== "units" ? ` (in ${input.unit_scale})` : "";
  return `${written}${input.currency ? ` ${input.currency}` : ""}${scale}`;
}

function simplify(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

// The column the value was read from, only when it says something the label
// doesn't (e.g. "Three Months Ended June 27, 2026"): models often echo the
// row label or the sheet name there instead.
export function columnNote(input: RatioInput): string | null {
  const header = input.column_header?.trim();
  if (!header || /^sheet\b/i.test(header)) return null;
  const h = simplify(header);
  const label = simplify(input.label ?? "");
  if (!h || (label && (h.includes(label) || label.includes(h)))) return null;
  return header;
}

interface RatiosViewProps {
  onCitationClick: (citation: CitationOut) => void;
  // Reload the document list whenever the view is shown (new uploads, approvals).
  active?: boolean;
}

export default function RatiosView({ onCitationClick, active = true }: RatiosViewProps) {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [categories, setCategories] = useState<RatioCategory[]>(CATEGORIES.map((c) => c.key));
  const [period, setPeriod] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [report, setReport] = useState<RatioReport | null>(null);
  // Ratios held for review whose mapping the analyst has confirmed.
  const [reviewed, setReviewed] = useState<string[]>([]);

  useEffect(() => {
    if (!active) return;
    listDocuments()
      .then((list) => {
        const ready = list.documents.filter((doc) => doc.status === "ready");
        setDocuments(ready);
        setSelected((prev) => prev.filter((id) => ready.some((doc) => doc.id === id)));
      })
      .catch(() => setErrorMessage("Could not load the document list."));
  }, [active]);

  function toggleDocument(documentId: string) {
    setSelected((prev) =>
      prev.includes(documentId) ? prev.filter((id) => id !== documentId) : [...prev, documentId]
    );
  }

  function toggleCategory(category: RatioCategory) {
    setCategories((prev) =>
      prev.includes(category) ? prev.filter((c) => c !== category) : [...prev, category]
    );
  }

  async function run() {
    setStatus("loading");
    setErrorMessage(null);
    try {
      const result = await calculateRatios({
        document_ids: selected,
        period: period.trim() || null,
        categories,
      });
      setReport(result);
      setReviewed([]);
      setStatus("idle");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "The ratio calculation failed.");
      setStatus("error");
    }
  }

  const canRun = selected.length >= 1 && selected.length <= 6 && categories.length > 0 && status !== "loading";

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1rem" }}>
      <section style={panelStyle} aria-label="Ratio setup">
        <div>
          <h2 style={sectionTitleStyle}>Statements</h2>
          <p style={hintStyle}>
            Select the statements to calculate from (e.g. the income statement and the balance sheet). Each input is
            looked up across all of them.
          </p>
          {documents.length === 0 && <p style={hintStyle}>No processed documents yet. Upload some from Documents.</p>}
          <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem", maxHeight: "14rem", overflowY: "auto" }}>
            {documents.map((doc) => (
              <label key={doc.id} style={documentOptionStyle}>
                <input
                  type="checkbox"
                  checked={selected.includes(doc.id)}
                  onChange={() => toggleDocument(doc.id)}
                  aria-label={`Use ${doc.filename}`}
                />
                <span style={{ flex: 1, wordBreak: "break-all" }}>{doc.filename}</span>
                <span style={chipStyle}>v{doc.version_number}</span>
                {!doc.is_current && <span style={{ ...chipStyle, color: "#64748b" }}>not current</span>}
              </label>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
          <fieldset style={{ border: "none", padding: 0, margin: 0, display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
            <legend style={{ fontSize: "0.8rem", color: "#334155", marginBottom: "0.25rem" }}>Categories</legend>
            {CATEGORIES.map((c) => (
              <label key={c.key} style={{ fontSize: "0.85rem", display: "flex", gap: "0.3rem", alignItems: "center" }}>
                <input type="checkbox" checked={categories.includes(c.key)} onChange={() => toggleCategory(c.key)} />
                {c.label}
              </label>
            ))}
          </fieldset>
          <label style={fieldStyle}>
            Period (optional)
            <input
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              placeholder="e.g. FY2024"
              style={inputStyle}
            />
          </label>
          <button type="button" onClick={run} disabled={!canRun} style={primaryButtonStyle(canRun)}>
            {status === "loading" ? "Calculating..." : "Calculate ratios"}
          </button>
        </div>
        {status === "loading" && (
          <p style={hintStyle}>Finding each line item in the statements and checking it against its source…</p>
        )}
        {errorMessage && <p style={{ color: "#b91c1c", fontSize: "0.85rem", margin: 0 }}>{errorMessage}</p>}
      </section>

      {report && (
        <section aria-label="Ratios" style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {report.notes.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "0.82rem", color: "#475569" }}>
              {report.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {CATEGORIES.filter((c) => report.ratios.some((r) => r.category === c.key)).map((c) => (
            <div key={c.key} style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              <h2 style={{ ...sectionTitleStyle, fontSize: "1.05rem" }}>{c.label}</h2>
              {report.ratios
                .filter((r) => r.category === c.key)
                .map((ratio) => (
                  <RatioCard
                    key={ratio.key}
                    ratio={ratio}
                    reviewed={reviewed.includes(ratio.key)}
                    onConfirm={() => setReviewed((prev) => [...prev, ratio.key])}
                    onCitationClick={onCitationClick}
                  />
                ))}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

interface RatioCardProps {
  ratio: Ratio;
  reviewed: boolean;
  onConfirm: () => void;
  onCitationClick: (citation: CitationOut) => void;
}

function RatioCard({ ratio, reviewed, onConfirm, onCitationClick }: RatioCardProps) {
  const heldForReview = ratio.status === "needs_review" && !reviewed;
  return (
    <article aria-label={ratio.name} style={cardStyle(ratio.status, reviewed)}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "baseline" }}>
        <div>
          <div style={{ fontWeight: 600 }}>
            {ratio.name}
            {ratio.cross_document && <span style={{ ...chipStyle, marginLeft: "0.5rem" }}>cross-document</span>}
          </div>
          <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
            {ratio.formula}
            {ratio.period ? ` · ${ratio.period}` : ""}
          </div>
        </div>
        <div style={{ fontSize: "1.3rem", fontWeight: 700, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
          {ratio.status === "unavailable" ? (
            <span style={{ fontSize: "0.85rem", color: "#64748b", fontWeight: 400 }}>can't be calculated</span>
          ) : heldForReview ? (
            <span style={{ fontSize: "0.85rem", color: "#b45309", fontWeight: 600 }}>held for review</span>
          ) : (
            <span data-testid={`ratio-value-${ratio.key}`}>{formatRatio(ratio)}</span>
          )}
        </div>
      </div>

      {ratio.reasons.length > 0 && (
        <ul
          role={heldForReview ? "alert" : undefined}
          style={{
            margin: "0.4rem 0 0",
            paddingLeft: "1.1rem",
            fontSize: "0.8rem",
            color: ratio.status === "unavailable" ? "#475569" : "#b45309",
          }}
        >
          {ratio.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {heldForReview && (
        <button type="button" onClick={onConfirm} style={{ ...secondaryButtonStyle, marginTop: "0.4rem" }}>
          I've reviewed it — show ratio
        </button>
      )}

      <table style={tableStyle} aria-label={`${ratio.name} inputs`}>
        <tbody>
          {ratio.inputs.map((input) => (
            <tr key={input.key} style={input.needs_review ? { background: "#fffbeb" } : undefined}>
              <td style={tdStyle}>{input.name}</td>
              <td style={{ ...tdStyle, color: "#475569" }}>
                {input.found ? (
                  <>
                    {input.label ?? "—"}
                    {columnNote(input) && (
                      <div style={{ fontSize: "0.72rem", color: "#64748b" }}>{columnNote(input)}</div>
                    )}
                    {input.needs_review && <span style={{ ...chipStyle, ...lowChipStyle }}>low-confidence mapping</span>}
                  </>
                ) : (
                  <span style={{ color: "#64748b" }}>not found</span>
                )}
              </td>
              <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                {input.found ? formatInputValue(input) : "—"}
              </td>
              <td style={tdStyle}>
                {input.citation && <CitationBadge citation={input.citation} onClick={onCitationClick} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  );
}

function cardStyle(status: Ratio["status"], reviewed: boolean): CSSProperties {
  const border = status === "needs_review" && !reviewed ? "#fcd34d" : "#e2e8f0";
  return { border: `1px solid ${border}`, borderRadius: "0.6rem", padding: "0.75rem 0.9rem", background: "white" };
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
  fontWeight: 400,
};
const lowChipStyle: CSSProperties = { marginLeft: "0.4rem", background: "#fef3c7", color: "#92400e", borderColor: "#fcd34d" };
const fieldStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.8rem", color: "#334155" };
const inputStyle: CSSProperties = { padding: "0.4rem 0.55rem", border: "1px solid #cbd5e1", borderRadius: "0.4rem", fontSize: "0.9rem", minWidth: "10rem" };
const secondaryButtonStyle: CSSProperties = {
  padding: "0.3rem 0.7rem",
  borderRadius: "0.4rem",
  border: "1px solid #b45309",
  background: "white",
  color: "#b45309",
  cursor: "pointer",
  fontSize: "0.8rem",
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
const tableStyle: CSSProperties = { borderCollapse: "collapse", width: "100%", fontSize: "0.82rem", marginTop: "0.5rem" };
const tdStyle: CSSProperties = { padding: "0.3rem 0.4rem", borderTop: "1px solid #f1f5f9", verticalAlign: "top" };
