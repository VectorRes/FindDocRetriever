import { useEffect, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { ApiError, exportAccessLogs, listAccessLogs } from "../api/client";
import type { AuditFilters, AuditLog, AuditLogPage } from "../api/types";

// ID-HU-BE-014: the access audit trail for administrators — every access
// attempt, granted or denied, filterable by user, document and time period.

const PAGE_SIZE = 25;
const QUESTION_PREVIEW = 90;

const RESOURCES = ["chat", "query", "ratios", "compare", "resolve", "cells", "file", "audit"];

interface FormState {
  user_id: string;
  document: string;
  from: string; // yyyy-mm-dd from the date input
  to: string;
  outcome: "all" | "granted" | "denied";
  tier: string;
  insider_only: boolean;
  resource: string;
}

const EMPTY_FORM: FormState = {
  user_id: "",
  document: "",
  from: "",
  to: "",
  outcome: "all",
  tier: "",
  insider_only: false,
  resource: "",
};

// The date inputs are days in the viewer's time zone: the whole day counts.
export function toFilters(form: FormState): AuditFilters {
  return {
    user_id: form.user_id,
    document: form.document,
    from: form.from ? new Date(`${form.from}T00:00:00`).toISOString() : undefined,
    to: form.to ? new Date(`${form.to}T23:59:59.999`).toISOString() : undefined,
    outcome: form.outcome,
    tier: form.tier,
    insider_only: form.insider_only,
    resource: form.resource || undefined,
  };
}

const dateFormat = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "medium" });

interface AuditViewProps {
  // Reload whenever the view is shown, so new attempts appear.
  active?: boolean;
}

export default function AuditView({ active = true }: AuditViewProps) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [applied, setApplied] = useState<AuditFilters>(toFilters(EMPTY_FORM));
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditLogPage | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setStatus("loading");
    setErrorMessage(null);
    listAccessLogs(applied, { limit: PAGE_SIZE, offset })
      .then((result) => {
        if (cancelled) return;
        setPage(result);
        setStatus("idle");
      })
      .catch((err) => {
        if (cancelled) return;
        setPage(null);
        setStatus("error");
        setErrorMessage(
          err instanceof ApiError && err.status === 403
            ? "The audit trail is only available to administrators."
            : err instanceof ApiError
              ? err.message
              : "Could not load the audit trail."
        );
      });
    return () => {
      cancelled = true;
    };
  }, [active, applied, offset]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function apply(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setApplied(toFilters(form));
  }

  function reset() {
    setForm(EMPTY_FORM);
    setOffset(0);
    setApplied(toFilters(EMPTY_FORM));
  }

  async function download() {
    try {
      const blob = await exportAccessLogs(applied);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "access-audit.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "The export failed.");
    }
  }

  const total = page?.total ?? 0;
  const lastShown = Math.min(offset + PAGE_SIZE, total);

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1rem" }}>
      <form style={panelStyle} aria-label="Audit filters" onSubmit={apply}>
        <div>
          <h2 style={sectionTitleStyle}>Access audit trail</h2>
          <p style={hintStyle}>
            Every access attempt, granted or denied, with the user, document, confidentiality tier and time.
          </p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
          <label style={fieldStyle}>
            User
            <input style={inputStyle} value={form.user_id} onChange={(e) => update("user_id", e.target.value)} />
          </label>
          <label style={fieldStyle}>
            Document
            <input
              style={inputStyle}
              value={form.document}
              placeholder="e.g. Legal_Contingencies_FY24.pdf"
              onChange={(e) => update("document", e.target.value)}
            />
          </label>
          <label style={fieldStyle}>
            From
            <input type="date" style={inputStyle} value={form.from} onChange={(e) => update("from", e.target.value)} />
          </label>
          <label style={fieldStyle}>
            To
            <input type="date" style={inputStyle} value={form.to} onChange={(e) => update("to", e.target.value)} />
          </label>
          <label style={fieldStyle}>
            Outcome
            <select
              style={inputStyle}
              value={form.outcome}
              onChange={(e) => update("outcome", e.target.value as FormState["outcome"])}
            >
              <option value="all">All</option>
              <option value="denied">Denied</option>
              <option value="granted">Granted</option>
            </select>
          </label>
          <label style={fieldStyle}>
            Tier
            <input
              style={inputStyle}
              value={form.tier}
              placeholder="e.g. restricted:compensation"
              onChange={(e) => update("tier", e.target.value)}
            />
          </label>
          <label style={fieldStyle}>
            Origin
            <select style={inputStyle} value={form.resource} onChange={(e) => update("resource", e.target.value)}>
              <option value="">Any</option>
              {RESOURCES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <label style={{ ...fieldStyle, flexDirection: "row", alignItems: "center", alignSelf: "flex-end" }}>
            <input
              type="checkbox"
              checked={form.insider_only}
              onChange={(e) => update("insider_only", e.target.checked)}
            />
            Pre-release insider only
          </label>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button type="submit" style={primaryButtonStyle}>
            Apply filters
          </button>
          <button type="button" style={secondaryButtonStyle} onClick={reset}>
            Clear
          </button>
          <button type="button" style={secondaryButtonStyle} onClick={download} disabled={status === "error"}>
            Export CSV
          </button>
        </div>
      </form>

      {errorMessage && (
        <p role="alert" style={errorStyle}>
          {errorMessage}
        </p>
      )}

      {page && (
        <section style={panelStyle} aria-label="Access attempts">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={hintStyle}>
              {total === 0 ? "No access attempts match these filters." : `${offset + 1}–${lastShown} of ${total}`}
            </span>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                type="button"
                style={secondaryButtonStyle}
                disabled={offset === 0 || status === "loading"}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                type="button"
                style={secondaryButtonStyle}
                disabled={lastShown >= total || status === "loading"}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          </div>
          {page.items.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    {["Time", "User", "Outcome", "Document", "Tier", "Origin", "Reason", "Question"].map((h) => (
                      <th key={h} style={thStyle}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((log) => (
                    <AuditRow key={log.id} log={log} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {status === "loading" && !page && <p style={hintStyle}>Loading…</p>}
    </div>
  );
}

function AuditRow({ log }: { log: AuditLog }) {
  const long = log.question.length > QUESTION_PREVIEW;
  return (
    <tr>
      <td style={tdStyle}>{dateFormat.format(new Date(log.occurred_at))}</td>
      <td style={tdStyle}>
        <div>{log.user_id}</div>
        <div style={mutedStyle}>{log.roles.join(", ")}</div>
      </td>
      <td style={tdStyle}>
        <span style={{ ...chipStyle, ...(log.authorized ? grantedChipStyle : deniedChipStyle) }}>
          {log.authorized ? "granted" : "denied"}
        </span>
      </td>
      <td style={tdStyle}>{log.document_filename ?? "—"}</td>
      <td style={tdStyle}>
        {log.confidentiality_tag}
        {log.is_insider && <span style={{ ...chipStyle, ...deniedChipStyle, marginLeft: "0.35rem" }}>insider</span>}
      </td>
      <td style={tdStyle}>
        <div>{log.resource}</div>
        <div style={mutedStyle}>{log.action}</div>
      </td>
      <td style={tdStyle}>{log.reason ?? "—"}</td>
      <td style={{ ...tdStyle, maxWidth: "18rem" }}>
        {long ? (
          <details>
            <summary>{log.question.slice(0, QUESTION_PREVIEW)}…</summary>
            {log.question}
          </details>
        ) : (
          log.question || "—"
        )}
      </td>
    </tr>
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
const hintStyle: CSSProperties = { fontSize: "0.8rem", color: "#64748b", margin: 0 };
const mutedStyle: CSSProperties = { fontSize: "0.72rem", color: "#64748b" };
const fieldStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.8rem", color: "#334155" };
const inputStyle: CSSProperties = { padding: "0.4rem 0.55rem", border: "1px solid #cbd5e1", borderRadius: "0.4rem", fontSize: "0.85rem", minWidth: "9rem" };
const primaryButtonStyle: CSSProperties = {
  padding: "0.4rem 0.9rem",
  borderRadius: "0.4rem",
  border: "1px solid #4f46e5",
  background: "#4f46e5",
  color: "white",
  cursor: "pointer",
  fontSize: "0.85rem",
};
const secondaryButtonStyle: CSSProperties = {
  padding: "0.4rem 0.8rem",
  borderRadius: "0.4rem",
  border: "1px solid #cbd5e1",
  background: "white",
  color: "#334155",
  cursor: "pointer",
  fontSize: "0.85rem",
};
const errorStyle: CSSProperties = {
  margin: 0,
  padding: "0.6rem 0.8rem",
  borderRadius: "0.5rem",
  background: "#fef2f2",
  color: "#b91c1c",
  fontSize: "0.85rem",
};
const chipStyle: CSSProperties = {
  fontSize: "0.7rem",
  padding: "0.05rem 0.45rem",
  borderRadius: "999px",
  border: "1px solid #e2e8f0",
  whiteSpace: "nowrap",
};
const grantedChipStyle: CSSProperties = { background: "#f0fdf4", color: "#15803d", borderColor: "#86efac" };
const deniedChipStyle: CSSProperties = { background: "#fef2f2", color: "#b91c1c", borderColor: "#fca5a5" };
const tableStyle: CSSProperties = { borderCollapse: "collapse", width: "100%", fontSize: "0.8rem" };
const thStyle: CSSProperties = { textAlign: "left", padding: "0.3rem 0.4rem", color: "#475569", fontWeight: 600, whiteSpace: "nowrap" };
const tdStyle: CSSProperties = { padding: "0.35rem 0.4rem", borderTop: "1px solid #f1f5f9", verticalAlign: "top" };
