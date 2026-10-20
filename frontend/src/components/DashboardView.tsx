import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { ApiError, exportDashboard, getDashboard, getDashboardCatalog, refreshDashboard } from "../api/client";
import type {
  CatalogEntity,
  CitationOut,
  Dashboard,
  DashboardPoint,
  DashboardRatio,
  Driver,
  RatioCategory,
  RatioInput,
  Statement,
} from "../api/types";
import CitationBadge from "./CitationBadge";
import { formatInputValue } from "./RatiosView";
import TrendChart, { formatRatioValue } from "./TrendChart";

// ID-HU-FE-004: key figures and ratios by period, with their sources, the
// trend over time and outliers to review. Reading it uses stored results;
// "Calculate" recalculates the chosen periods with the ratio engine (BE-010).

const CATEGORIES: { key: RatioCategory; label: string }[] = [
  { key: "liquidity", label: "Liquidity" },
  { key: "solvency", label: "Solvency" },
  { key: "profitability", label: "Profitability" },
  { key: "efficiency", label: "Efficiency" },
];
const KEY_RATIOS = ["roe", "roa", "ebitda_margin", "current_ratio"];
const STATEMENT_LABELS: Record<Statement, string> = {
  income_statement: "income statement",
  balance_sheet: "balance sheet",
};

export function pointText(point: DashboardPoint, unit: "percent" | "times", reviewed = false): string {
  switch (point.status) {
    case "calculated":
      return formatRatioValue(point.value, unit);
    case "needs_review":
      return reviewed ? formatRatioValue(point.value, unit) : "Held for review";
    case "unavailable":
      return "Insufficient data";
    case "not_calculated":
      return "Not calculated";
    case "restricted":
      return "Restricted";
  }
}

// Change against the previous calculated period, in the ratio's unit
// (percentage points or "x"), or null when there's nothing to compare.
export function changeText(ratio: DashboardRatio, period: string): string | null {
  const index = ratio.points.findIndex((p) => p.period === period);
  const current = ratio.points[index];
  if (!current || current.status !== "calculated" || current.value == null) return null;
  const previous = ratio.points
    .slice(0, index)
    .reverse()
    .find((p) => p.status === "calculated" && p.value != null);
  if (!previous || previous.value == null) return null;
  const delta = current.value - previous.value;
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
  const size =
    ratio.unit === "percent" ? `${Math.abs(delta * 100).toFixed(1)} pp` : `${Math.abs(delta).toFixed(2)}x`;
  return `${sign}${size} vs ${previous.period}`;
}

function driverValue(driver: Driver, value: number | null): string {
  if (value == null) return "—";
  if (driver.unit === "amount") return new Intl.NumberFormat("en-US").format(value);
  return formatRatioValue(value, driver.unit);
}

interface DashboardViewProps {
  onCitationClick: (citation: CitationOut) => void;
  // Reload the catalog whenever the view is shown (new uploads, approvals).
  active?: boolean;
}

export default function DashboardView({ onCitationClick, active = true }: DashboardViewProps) {
  const [catalog, setCatalog] = useState<CatalogEntity[]>([]);
  // ?entity=Andina opens that entity directly (e.g. /?entity=Pacifico#dashboard).
  const [entity, setEntity] = useState(() => new URLSearchParams(window.location.search).get("entity") ?? "");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  // Periods shown and recalculated; empty = all of them.
  const [chosenPeriods, setChosenPeriods] = useState<string[]>([]);
  const [extraPeriods, setExtraPeriods] = useState<string[]>([]);
  const [newPeriod, setNewPeriod] = useState("");
  const [categories, setCategories] = useState<RatioCategory[]>(CATEGORIES.map((c) => c.key));
  const [selectedRatio, setSelectedRatio] = useState("roe");
  const [selectedPeriod, setSelectedPeriod] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "refreshing" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    getDashboardCatalog()
      .then((result) => {
        setCatalog(result.entities);
        setEntity((current) =>
          result.entities.find((e) => e.entity.toLowerCase() === current.toLowerCase())?.entity ??
          result.entities[0]?.entity ??
          ""
        );
      })
      .catch(() => setErrorMessage("Could not load the entities."));
  }, [active]);

  useEffect(() => {
    if (!active || !entity) return;
    let cancelled = false;
    setStatus("loading");
    setErrorMessage(null);
    getDashboard(entity)
      .then((result) => {
        if (cancelled) return;
        show(result);
        setStatus("idle");
      })
      .catch((err) => {
        if (cancelled) return;
        setDashboard(null);
        setStatus("error");
        setErrorMessage(err instanceof ApiError ? err.message : "Could not load the dashboard.");
      });
    return () => {
      cancelled = true;
    };
  }, [active, entity]);

  function show(result: Dashboard) {
    setDashboard(result);
    setReviewed([]);
    const calculated = result.periods.filter((p) => p.calculated && !p.restricted);
    setSelectedPeriod((current) =>
      current && result.periods.some((p) => p.period === current)
        ? current
        : (calculated[calculated.length - 1] ?? result.periods[result.periods.length - 1])?.period ?? null
    );
  }

  function changeEntity(next: string) {
    setEntity(next);
    setChosenPeriods([]);
    setExtraPeriods([]);
    setSelectedPeriod(null);
  }

  async function calculate() {
    setStatus("refreshing");
    setErrorMessage(null);
    try {
      show(await refreshDashboard(entity, chosenPeriods));
      setStatus("idle");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "The calculation failed.");
      setStatus("error");
    }
  }

  async function download() {
    if (!visible) return;
    try {
      const blob = await exportDashboard(visible);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `ratios-dashboard-${dashboard?.entity_key.replace(/\s+/g, "-") ?? "entity"}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "The export failed.");
    }
  }

  function addPeriod() {
    const period = newPeriod.trim();
    if (!period) return;
    setExtraPeriods((prev) => (prev.includes(period) ? prev : [...prev, period]));
    setChosenPeriods((prev) => (prev.includes(period) ? prev : [...prev, period]));
    setNewPeriod("");
  }

  function togglePeriod(period: string) {
    setChosenPeriods((prev) => (prev.includes(period) ? prev.filter((p) => p !== period) : [...prev, period]));
  }

  function toggleCategory(category: RatioCategory) {
    setCategories((prev) => (prev.includes(category) ? prev.filter((c) => c !== category) : [...prev, category]));
  }

  const catalogEntity = catalog.find((e) => e.entity === entity) ?? null;
  const allPeriods = useMemo(() => {
    const known = dashboard?.periods.map((p) => p.period) ?? catalogEntity?.periods ?? [];
    return [...known, ...extraPeriods.filter((p) => !known.includes(p))];
  }, [dashboard, catalogEntity, extraPeriods]);

  // The dashboard as shown: the chosen periods and categories only.
  const visible: Dashboard | null = useMemo(() => {
    if (!dashboard) return null;
    const keep = (period: string) => chosenPeriods.length === 0 || chosenPeriods.includes(period);
    return {
      ...dashboard,
      periods: dashboard.periods.filter((p) => keep(p.period)),
      ratios: dashboard.ratios
        .filter((r) => categories.includes(r.category))
        .map((r) => ({ ...r, points: r.points.filter((p) => keep(p.period)) })),
    };
  }, [dashboard, chosenPeriods, categories]);

  const period = visible?.periods.some((p) => p.period === selectedPeriod)
    ? selectedPeriod
    : visible?.periods[visible.periods.length - 1]?.period ?? null;
  const ratio = visible?.ratios.find((r) => r.key === selectedRatio) ?? visible?.ratios[0] ?? null;
  const busy = status === "loading" || status === "refreshing";

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1rem" }}>
      <section className="no-print" style={panelStyle} aria-label="Dashboard setup">
        <div>
          <h2 style={sectionTitleStyle}>Key ratios dashboard</h2>
          <p style={hintStyle}>
            Ratios calculated from each company's ingested statements, period by period, with the source of every
            figure. Calculating takes about 20 seconds per period; opening the dashboard shows the stored results.
          </p>
        </div>
        {catalog.length === 0 ? (
          <p style={hintStyle}>No processed statements you can access yet. Upload some from Documents.</p>
        ) : (
          <>
            <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
              <label style={fieldStyle}>
                Entity
                <select value={entity} onChange={(e) => changeEntity(e.target.value)} style={inputStyle} aria-label="Entity">
                  {catalog.map((e) => (
                    <option key={e.key} value={e.entity}>
                      {e.entity}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset style={fieldsetStyle}>
                <legend style={legendStyle}>Periods {chosenPeriods.length === 0 && "(all)"}</legend>
                {allPeriods.map((p) => (
                  <label key={p} style={checkLabelStyle}>
                    <input type="checkbox" checked={chosenPeriods.includes(p)} onChange={() => togglePeriod(p)} />
                    {p}
                  </label>
                ))}
                <input
                  value={newPeriod}
                  onChange={(e) => setNewPeriod(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addPeriod()}
                  placeholder="add, e.g. 2022"
                  aria-label="Add a period"
                  style={{ ...inputStyle, minWidth: "7rem", width: "7rem" }}
                />
                <button type="button" onClick={addPeriod} style={secondaryButtonStyle}>
                  Add
                </button>
              </fieldset>
              <fieldset style={fieldsetStyle}>
                <legend style={legendStyle}>Categories</legend>
                {CATEGORIES.map((c) => (
                  <label key={c.key} style={checkLabelStyle}>
                    <input type="checkbox" checked={categories.includes(c.key)} onChange={() => toggleCategory(c.key)} />
                    {c.label}
                  </label>
                ))}
              </fieldset>
            </div>
            {catalogEntity && <EntityDocuments entity={catalogEntity} />}
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button type="button" onClick={calculate} disabled={!entity || busy} style={primaryButtonStyle(!busy)}>
                {status === "refreshing"
                  ? "Calculating…"
                  : chosenPeriods.length
                    ? `Calculate ${chosenPeriods.join(", ")}`
                    : "Calculate all periods"}
              </button>
              <button type="button" onClick={download} disabled={!visible} style={secondaryButtonStyle}>
                Export to Excel
              </button>
              <button type="button" onClick={() => window.print()} disabled={!visible} style={secondaryButtonStyle}>
                Save as PDF
              </button>
            </div>
            {status === "refreshing" && (
              <p style={hintStyle}>Finding each line item in the statements and checking it against its source…</p>
            )}
          </>
        )}
        {errorMessage && (
          <p role="alert" style={errorStyle}>
            {errorMessage}
          </p>
        )}
      </section>

      {visible && (
        <>
          <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "1rem" }}>
            <h2 style={{ margin: 0, fontSize: "1.2rem" }}>{visible.entity}</h2>
            {period && (
              <label className="no-print" style={{ ...fieldStyle, flexDirection: "row", alignItems: "center" }}>
                Showing
                <select value={period} onChange={(e) => setSelectedPeriod(e.target.value)} style={inputStyle} aria-label="Period shown">
                  {visible.periods.map((p) => (
                    <option key={p.period} value={p.period}>
                      {p.period}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </header>

          {(visible.notes.length > 0 || visible.periods.some((p) => p.notes.length || p.restricted)) && (
            <ul style={notesStyle} aria-label="Notes">
              {visible.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
              {visible.periods.map((p) =>
                p.restricted ? (
                  <li key={`${p.period}-restricted`}>
                    {p.period}: calculated from a document your role can't access, so its values aren't shown.
                  </li>
                ) : (
                  p.notes.map((note) => <li key={`${p.period}-${note}`}>{`${p.period}: ${note}`}</li>)
                )
              )}
            </ul>
          )}

          {period && (
            <section aria-label="Key ratios" style={tilesStyle}>
              {KEY_RATIOS.map((key) => visible.ratios.find((r) => r.key === key))
                .filter((r): r is DashboardRatio => r != null)
                .map((r) => (
                  <KeyTile key={r.key} ratio={r} period={period} selected={ratio?.key === r.key} onSelect={() => setSelectedRatio(r.key)} />
                ))}
            </section>
          )}

          {ratio && period && (
            <RatioDetail
              ratio={ratio}
              period={period}
              reviewed={reviewed.includes(`${ratio.key}|${period}`)}
              onReviewed={() => setReviewed((prev) => [...prev, `${ratio.key}|${period}`])}
              onSelectPeriod={setSelectedPeriod}
              onCitationClick={onCitationClick}
            />
          )}

          {period &&
            CATEGORIES.filter((c) => visible.ratios.some((r) => r.category === c.key)).map((c) => (
              <section key={c.key} aria-label={`${c.label} ratios`} style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                <h3 style={{ ...sectionTitleStyle, fontSize: "1rem" }}>{c.label}</h3>
                <div style={gridStyle}>
                  {visible.ratios
                    .filter((r) => r.category === c.key)
                    .map((r) => (
                      <RatioCard
                        key={r.key}
                        ratio={r}
                        period={period}
                        selected={ratio?.key === r.key}
                        onSelect={() => setSelectedRatio(r.key)}
                      />
                    ))}
                </div>
              </section>
            ))}
        </>
      )}
      {status === "loading" && !dashboard && <p style={hintStyle}>Loading…</p>}
    </div>
  );
}

function EntityDocuments({ entity }: { entity: CatalogEntity }) {
  return (
    <details style={{ fontSize: "0.8rem", color: "#475569" }}>
      <summary style={{ cursor: "pointer" }}>
        {entity.documents.length} document{entity.documents.length === 1 ? "" : "s"} for {entity.entity}
      </summary>
      <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>
        {entity.documents.map((d) => (
          <li key={d.id}>
            {d.filename}
            <span style={{ color: "#64748b" }}>
              {" — "}
              {d.statements.length ? d.statements.map((s) => STATEMENT_LABELS[s]).join(" + ") : "statement not detected"}
              {d.periods.length ? ` · ${d.periods.join(", ")}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function pointAt(ratio: DashboardRatio, period: string): DashboardPoint | undefined {
  return ratio.points.find((p) => p.period === period);
}

function StatusChips({ point, detail = false }: { point: DashboardPoint; detail?: boolean }) {
  return (
    <>
      {!detail && point.missing_statements.length > 0 && (
        <span style={{ ...chipStyle, ...staleChipStyle }}>
          missing {point.missing_statements.map((s) => STATEMENT_LABELS[s]).join(" + ")}
        </span>
      )}
      {point.outlier && <span style={{ ...chipStyle, ...outlierChipStyle }}>⚠ outlier</span>}
      {point.stale && <span style={{ ...chipStyle, ...staleChipStyle }}>outdated</span>}
      {detail && point.cross_document && <span style={chipStyle}>cross-document: {sourceCount(point)} sources</span>}
    </>
  );
}

function sourceCount(point: DashboardPoint): number {
  return new Set(point.inputs.map((i) => i.document_filename).filter(Boolean)).size;
}

interface TileProps {
  ratio: DashboardRatio;
  period: string;
  selected: boolean;
  onSelect: () => void;
}

function KeyTile({ ratio, period, selected, onSelect }: TileProps) {
  const point = pointAt(ratio, period);
  const change = changeText(ratio, period);
  return (
    <button type="button" onClick={onSelect} aria-pressed={selected} aria-label={`${ratio.name} tile`} style={tileStyle(selected)}>
      <span style={{ fontSize: "0.8rem", color: "#52514e" }}>{ratio.name}</span>
      <span data-testid={`tile-value-${ratio.key}`} style={{ fontSize: point?.status === "calculated" ? "1.6rem" : "1rem", fontWeight: 600, color: "#0b0b0b" }}>
        {point ? pointText(point, ratio.unit) : "—"}
      </span>
      <span style={{ fontSize: "0.75rem", color: "#52514e", minHeight: "1rem" }}>{change ?? " "}</span>
      <TrendChart points={ratio.points} unit={ratio.unit} title={ratio.name} compact />
      {point && (
        <span style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
          <StatusChips point={point} />
        </span>
      )}
    </button>
  );
}

function RatioCard({ ratio, period, selected, onSelect }: TileProps) {
  const point = pointAt(ratio, period);
  return (
    <button type="button" onClick={onSelect} aria-pressed={selected} aria-label={`${ratio.name} card`} style={cardStyle(selected)}>
      <span style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "baseline" }}>
        <span style={{ fontWeight: 600, fontSize: "0.85rem", textAlign: "left" }}>{ratio.name}</span>
        <span data-testid={`card-value-${ratio.key}`} style={{ fontWeight: 600, whiteSpace: "nowrap", fontSize: point?.status === "calculated" ? "1rem" : "0.78rem", color: point?.status === "calculated" ? "#0b0b0b" : "#52514e" }}>
          {point ? pointText(point, ratio.unit) : "—"}
        </span>
      </span>
      <TrendChart points={ratio.points} unit={ratio.unit} title={ratio.name} compact />
      {point && (
        <span style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
          <StatusChips point={point} />
        </span>
      )}
    </button>
  );
}

interface RatioDetailProps {
  ratio: DashboardRatio;
  period: string;
  reviewed: boolean;
  onReviewed: () => void;
  onSelectPeriod: (period: string) => void;
  onCitationClick: (citation: CitationOut) => void;
}

function RatioDetail({ ratio, period, reviewed, onReviewed, onSelectPeriod, onCitationClick }: RatioDetailProps) {
  const point = pointAt(ratio, period);
  const outliers = ratio.points.filter((p) => p.outlier);
  const byDocument = new Map<string, RatioInput[]>();
  for (const input of point?.inputs ?? []) {
    const key = input.document_filename ?? "Not found in any document";
    byDocument.set(key, [...(byDocument.get(key) ?? []), input]);
  }
  const insufficient = point?.status === "unavailable";
  return (
    <section aria-label={`${ratio.name} detail`} style={panelStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "baseline", flexWrap: "wrap" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: "1.05rem" }}>{ratio.name}</h3>
          <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
            {ratio.formula} · {ratio.description}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div data-testid="detail-value" style={{ fontSize: "1.4rem", fontWeight: 600 }}>
            {point ? pointText(point, ratio.unit, reviewed) : "—"}
          </div>
          <div style={{ fontSize: "0.75rem", color: "#52514e" }}>
            {period}
            {changeText(ratio, period) ? ` · ${changeText(ratio, period)}` : ""}
          </div>
        </div>
      </div>

      {ratio.points.some((p) => p.status === "calculated") ? (
        <TrendChart points={ratio.points} unit={ratio.unit} title={ratio.name} selectedPeriod={period} onSelectPeriod={onSelectPeriod} />
      ) : (
        <p style={hintStyle}>No calculated value to plot for the periods shown.</p>
      )}

      {outliers.map((p) => (
        <div key={p.period} role="alert" style={outlierBoxStyle}>
          <div style={{ fontWeight: 600 }}>⚠ Outlier in {p.period} — review before presenting</div>
          <div>{p.outlier?.message}</div>
          {p.outlier && p.outlier.drivers.length > 0 && (
            <table style={tableStyle} aria-label={`${ratio.name} drivers ${p.period}`}>
              <thead>
                <tr>
                  <th style={thStyle}>Driver</th>
                  <th style={thNumStyle}>{p.outlier.previous_period ?? "Before"}</th>
                  <th style={thNumStyle}>{p.period}</th>
                  <th style={thNumStyle}>Change</th>
                </tr>
              </thead>
              <tbody>
                {p.outlier.drivers.map((d) => (
                  <tr key={d.name}>
                    <td style={tdStyle}>
                      {d.name}
                      {d.note && <span style={{ color: "#64748b" }}> ({d.note})</span>}
                    </td>
                    <td style={tdNumStyle}>{driverValue(d, d.previous)}</td>
                    <td style={tdNumStyle}>{driverValue(d, d.current)}</td>
                    <td style={tdNumStyle}>{d.change == null ? "—" : `${d.change > 0 ? "+" : ""}${(d.change * 100).toFixed(0)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}

      {point && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <div style={{ display: "flex", gap: "0.4rem", alignItems: "center", flexWrap: "wrap" }}>
            <h4 style={{ margin: 0, fontSize: "0.9rem" }}>Sources for {period}</h4>
            <StatusChips point={point} detail />
          </div>
          {insufficient && (
            <div role="status" style={insufficientStyle}>
              <strong>Insufficient data</strong> — this doesn't mean "no activity".
              {point.missing_statements.length > 0 && (
                <div>
                  Missing document: the {point.missing_statements.map((s) => STATEMENT_LABELS[s]).join(" and the ")} for{" "}
                  {period}. Upload it and calculate again.
                </div>
              )}
            </div>
          )}
          {point.status === "not_calculated" && (
            <p style={hintStyle}>This period hasn't been calculated yet. Use “Calculate” above.</p>
          )}
          {point.reasons.length > 0 && point.missing_statements.length === 0 && (
            <ul style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "0.8rem", color: point.status === "needs_review" ? "#b45309" : "#475569" }}>
              {point.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {point.status === "needs_review" && !reviewed && (
            <button type="button" onClick={onReviewed} style={{ ...secondaryButtonStyle, alignSelf: "flex-start" }}>
              I've reviewed it — show value
            </button>
          )}
          {[...byDocument.entries()].map(([filename, inputs]) => (
            <div key={filename} style={sourceGroupStyle}>
              <div style={{ fontSize: "0.8rem", fontWeight: 600 }}>{filename}</div>
              <table style={tableStyle} aria-label={`Inputs from ${filename}`}>
                <tbody>
                  {inputs.map((input) => (
                    <tr key={input.key}>
                      <td style={tdStyle}>{input.name}</td>
                      <td style={{ ...tdStyle, color: "#475569" }}>{input.found ? input.label ?? "—" : "not found"}</td>
                      <td style={tdNumStyle}>{input.found ? formatInputValue(input) : "—"}</td>
                      <td style={tdStyle}>
                        {input.citation && <CitationBadge citation={input.citation} onClick={onCitationClick} />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

const panelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "0.9rem",
  padding: "1rem",
  border: "1px solid #e2e8f0",
  borderRadius: "0.6rem",
  background: "white",
};
const sectionTitleStyle: CSSProperties = { fontSize: "0.95rem", margin: "0 0 0.25rem" };
const hintStyle: CSSProperties = { fontSize: "0.8rem", color: "#64748b", margin: 0 };
const fieldStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.8rem", color: "#334155" };
const inputStyle: CSSProperties = { padding: "0.35rem 0.5rem", border: "1px solid #cbd5e1", borderRadius: "0.4rem", fontSize: "0.85rem", minWidth: "9rem" };
const fieldsetStyle: CSSProperties = { border: "none", padding: 0, margin: 0, display: "flex", gap: "0.6rem", flexWrap: "wrap", alignItems: "center" };
const legendStyle: CSSProperties = { fontSize: "0.8rem", color: "#334155", marginBottom: "0.25rem" };
const checkLabelStyle: CSSProperties = { fontSize: "0.85rem", display: "flex", gap: "0.3rem", alignItems: "center" };
const notesStyle: CSSProperties = { margin: 0, paddingLeft: "1.1rem", fontSize: "0.82rem", color: "#475569" };
const errorStyle: CSSProperties = { margin: 0, padding: "0.5rem 0.75rem", borderRadius: "0.5rem", background: "#fef2f2", color: "#b91c1c", fontSize: "0.85rem" };
const tilesStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(10rem, 1fr))", gap: "0.75rem" };
const gridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(12rem, 1fr))", gap: "0.6rem" };
const chipStyle: CSSProperties = { fontSize: "0.7rem", padding: "0.05rem 0.45rem", borderRadius: "999px", border: "1px solid #e2e8f0", whiteSpace: "nowrap", color: "#334155", background: "white" };
const outlierChipStyle: CSSProperties = { background: "#fff7ed", borderColor: "#ec835a", color: "#9a3412" };
const staleChipStyle: CSSProperties = { background: "#f1f5f9", color: "#475569" };
const outlierBoxStyle: CSSProperties = { border: "1px solid #ec835a", background: "#fff7ed", borderRadius: "0.5rem", padding: "0.6rem 0.75rem", fontSize: "0.83rem", color: "#0b0b0b", display: "flex", flexDirection: "column", gap: "0.3rem" };
const insufficientStyle: CSSProperties = { border: "1px solid #cbd5e1", background: "#f8fafc", borderRadius: "0.5rem", padding: "0.6rem 0.75rem", fontSize: "0.83rem", color: "#334155" };
const sourceGroupStyle: CSSProperties = { border: "1px solid #f1f5f9", borderRadius: "0.5rem", padding: "0.5rem 0.6rem" };
const tableStyle: CSSProperties = { borderCollapse: "collapse", width: "100%", fontSize: "0.8rem" };
const thStyle: CSSProperties = { textAlign: "left", padding: "0.25rem 0.4rem", color: "#475569", fontWeight: 600 };
const thNumStyle: CSSProperties = { ...thStyle, textAlign: "right" };
const tdStyle: CSSProperties = { padding: "0.3rem 0.4rem", borderTop: "1px solid #f1f5f9", verticalAlign: "top" };
const tdNumStyle: CSSProperties = { ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" };
const secondaryButtonStyle: CSSProperties = {
  padding: "0.35rem 0.8rem",
  borderRadius: "0.4rem",
  border: "1px solid #cbd5e1",
  background: "white",
  color: "#334155",
  cursor: "pointer",
  fontSize: "0.85rem",
};

function primaryButtonStyle(enabled: boolean): CSSProperties {
  return {
    padding: "0.4rem 0.9rem",
    borderRadius: "0.4rem",
    border: "1px solid #4f46e5",
    background: enabled ? "#4f46e5" : "#a5b4fc",
    color: "white",
    cursor: enabled ? "pointer" : "not-allowed",
    fontSize: "0.85rem",
  };
}

function tileStyle(selected: boolean): CSSProperties {
  return {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: "0.2rem",
    padding: "0.75rem 0.85rem",
    borderRadius: "0.6rem",
    border: selected ? "2px solid #4f46e5" : "1px solid #e2e8f0",
    background: "white",
    cursor: "pointer",
    textAlign: "left",
    font: "inherit",
  };
}

function cardStyle(selected: boolean): CSSProperties {
  return { ...tileStyle(selected), padding: "0.55rem 0.7rem", gap: "0.3rem", alignItems: "stretch" };
}
