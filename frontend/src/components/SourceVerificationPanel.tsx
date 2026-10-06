import { useEffect, useState } from "react";
import { ApiError, documentFileUrl, getCell, resolveDocument } from "../api/client";
import type { CellOut, CitationOut, DocumentResolutionOut } from "../api/types";

const SINGLE_CELL_RE = /^[A-Za-z]+[0-9]+$/;

interface SourceVerificationPanelProps {
  citation: CitationOut;
  onClose: () => void;
}

export default function SourceVerificationPanel({ citation, onClose }: SourceVerificationPanelProps) {
  const [documentId, setDocumentId] = useState(citation.document_id);
  const [resolution, setResolution] = useState<DocumentResolutionOut | null>(null);
  const [status, setStatus] = useState<"loading" | "done" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [cell, setCell] = useState<CellOut | null>(null);

  // A new citation can point at a different document than the one currently shown.
  useEffect(() => {
    setDocumentId(citation.document_id);
  }, [citation]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setCell(null);

    resolveDocument(documentId)
      .then((result) => {
        if (cancelled) return;
        setResolution(result);
        setStatus("done");
      })
      .catch((err) => {
        if (cancelled) return;
        setErrorMessage(err instanceof ApiError ? err.message : "Could not load this document.");
        setStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => {
    if (status !== "done" || !resolution?.access.allowed) return;
    if (!citation.sheet_name || !citation.cell_range) return;
    if (!SINGLE_CELL_RE.test(citation.cell_range)) return;

    let cancelled = false;
    getCell(documentId, citation.sheet_name, citation.cell_range)
      .then((result) => {
        if (!cancelled) setCell(result);
      })
      .catch(() => {
        // Formula lookup is a nice-to-have here — the citation's sheet/cell text
        // still displays without it, so a failure here isn't fatal to the panel.
      });

    return () => {
      cancelled = true;
    };
  }, [status, resolution, documentId, citation.sheet_name, citation.cell_range]);

  return (
    <aside className="no-print" style={panelStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ fontSize: "1rem", margin: 0 }}>Source</h2>
        <button type="button" onClick={onClose} style={closeButtonStyle} aria-label="Close">
          ×
        </button>
      </div>

      {status === "loading" && <p style={{ color: "#64748b" }}>Loading source...</p>}
      {status === "error" && <p style={{ color: "#b91c1c" }}>{errorMessage}</p>}

      {status === "done" && resolution && !resolution.access.allowed && (
        <div style={bannerStyle("#fef2f2", "#b91c1c")}>
          <strong>Access restricted.</strong>{" "}
          {resolution.access.reason ?? "You don't have permission to view this document."}
        </div>
      )}

      {status === "done" && resolution && resolution.access.allowed && (
        <>
          {resolution.is_superseded && (
            <div style={bannerStyle("#fffbeb", "#b45309")}>
              A newer approved version of this document exists.
              {resolution.current_version && (
                <button
                  type="button"
                  onClick={() => setDocumentId(resolution.current_version!.id)}
                  style={switchVersionButtonStyle}
                >
                  View current version ({resolution.current_version.filename})
                </button>
              )}
            </div>
          )}

          <div>
            <div style={{ fontWeight: 600 }}>{resolution.document.filename}</div>
            <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
              Version {resolution.document.version_number} · {resolution.document.approval_status}
              {resolution.is_superseded && " · superseded"}
            </div>
            {citation.sheet_name && (
              <div style={{ fontSize: "0.9rem", color: "#334155" }}>
                Sheet <strong>{citation.sheet_name}</strong>
                {citation.cell_range && (
                  <>
                    {" "}
                    · cell(s) <strong>{citation.cell_range}</strong>
                  </>
                )}
              </div>
            )}
            {citation.page_number != null && (
              <div style={{ fontSize: "0.9rem", color: "#334155" }}>Page {citation.page_number}</div>
            )}
          </div>

          {cell && (
            <div style={cellCardStyle}>
              <div>
                Value: <strong>{cell.value ?? "(empty)"}</strong>
              </div>
              {cell.formula && (
                <div style={{ marginTop: "0.25rem" }}>
                  Formula: <code>{cell.formula}</code>
                </div>
              )}
              {cell.formula_references.length > 0 && (
                <div style={{ marginTop: "0.25rem", fontSize: "0.8rem", color: "#64748b" }}>
                  References: {cell.formula_references.join(", ")}
                </div>
              )}
            </div>
          )}

          {citation.page_number != null && (
            <iframe
              title="Original PDF page"
              src={`${documentFileUrl(documentId)}#page=${citation.page_number}`}
              style={{ width: "100%", height: "22rem", border: "1px solid #e2e8f0", borderRadius: "0.5rem" }}
            />
          )}

          <div style={{ fontSize: "0.85rem", color: "#475569", background: "#f8fafc", padding: "0.5rem", borderRadius: "0.4rem" }}>
            {citation.text}
          </div>

          <a
            href={documentFileUrl(documentId)}
            target="_blank"
            rel="noreferrer"
            style={openFileLinkStyle}
          >
            Open original file
          </a>
        </>
      )}
    </aside>
  );
}

const panelStyle = {
  width: "22rem",
  flexShrink: 0,
  display: "flex",
  flexDirection: "column" as const,
  gap: "0.75rem",
  padding: "1rem",
  borderLeft: "1px solid #e2e8f0",
  background: "#fff",
  height: "fit-content",
  position: "sticky" as const,
  top: "1rem",
};

const closeButtonStyle = {
  border: "none",
  background: "transparent",
  fontSize: "1.3rem",
  lineHeight: 1,
  cursor: "pointer",
  color: "#64748b",
};

const switchVersionButtonStyle = {
  display: "block",
  marginTop: "0.4rem",
  padding: "0.3rem 0.6rem",
  border: "1px solid #b45309",
  borderRadius: "0.4rem",
  background: "white",
  color: "#b45309",
  cursor: "pointer",
  fontSize: "0.8rem",
};

const cellCardStyle = {
  border: "1px solid #e2e8f0",
  borderRadius: "0.5rem",
  padding: "0.6rem 0.8rem",
  fontSize: "0.85rem",
  background: "#f8fafc",
};

const openFileLinkStyle = {
  textAlign: "center" as const,
  padding: "0.5rem",
  borderRadius: "0.5rem",
  border: "1px solid #4f46e5",
  color: "#4f46e5",
  textDecoration: "none",
  fontSize: "0.9rem",
};

function bannerStyle(background: string, color: string) {
  return {
    background,
    color,
    padding: "0.6rem 0.8rem",
    borderRadius: "0.5rem",
    fontSize: "0.85rem",
  };
}
