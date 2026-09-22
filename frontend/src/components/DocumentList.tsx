import { useEffect, useState } from "react";
import { ApiError, listDocuments } from "../api/client";
import type { DocumentOut } from "../api/types";
import DocumentUpload from "./DocumentUpload";

const STATUS_STYLES: Record<string, { background: string; color: string }> = {
  queued: { background: "#f1f5f9", color: "#475569" },
  processing: { background: "#eff6ff", color: "#1d4ed8" },
  ready: { background: "#f0fdf4", color: "#15803d" },
  failed: { background: "#fef2f2", color: "#b91c1c" },
};

export default function DocumentList() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [status, setStatus] = useState<"loading" | "done" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  function refresh() {
    setStatus("loading");
    listDocuments()
      .then((result) => {
        setDocuments(result.documents);
        setStatus("done");
      })
      .catch((err) => {
        setErrorMessage(err instanceof ApiError ? err.message : "Could not load documents.");
        setStatus("error");
      });
  }

  useEffect(refresh, []);

  function byId(id: string | null): DocumentOut | undefined {
    return id ? documents.find((doc) => doc.id === id) : undefined;
  }

  return (
    <section style={sectionStyle}>
      <h2 style={{ fontSize: "1rem", margin: "0 0 0.75rem" }}>Documents</h2>

      <DocumentUpload onUploaded={refresh} />

      <div style={{ marginTop: "1rem", display: "flex", flexDirection: "column", gap: "0.6rem" }}>
        {status === "loading" && documents.length === 0 && (
          <p style={{ color: "#64748b", fontSize: "0.85rem" }}>Loading documents...</p>
        )}
        {status === "error" && <p style={{ color: "#b91c1c", fontSize: "0.85rem" }}>{errorMessage}</p>}
        {status === "done" && documents.length === 0 && (
          <p style={{ color: "#64748b", fontSize: "0.85rem" }}>No documents uploaded yet.</p>
        )}

        {documents.map((doc) => {
          const statusStyle = STATUS_STYLES[doc.status] ?? STATUS_STYLES.queued;
          const supersededBy = byId(doc.superseded_by_id);

          return (
            <div key={doc.id} style={cardStyle}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.5rem" }}>
                <span style={{ fontWeight: 600, fontSize: "0.9rem", wordBreak: "break-all" }}>{doc.filename}</span>
                <span style={{ ...badgeStyle, ...statusStyle }}>{doc.status}</span>
              </div>

              <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                <span style={{ ...badgeStyle, ...(doc.is_current ? currentBadgeStyle : supersededBadgeStyle) }}>
                  {doc.is_current ? "current version" : "superseded"}
                </span>
                {doc.confidentiality_tag === "restricted" && (
                  <span style={{ ...badgeStyle, background: "#fef3c7", color: "#92400e" }}>restricted</span>
                )}
              </div>

              {!doc.is_current && supersededBy && (
                <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
                  Superseded by <strong>{supersededBy.filename}</strong>
                </div>
              )}

              {doc.status === "ready" && doc.doc_type === "excel" && (
                <div style={{ fontSize: "0.8rem", color: "#334155" }}>
                  Sheets: {doc.sheet_names.length > 0 ? doc.sheet_names.join(", ") : "none detected"}
                </div>
              )}

              {doc.warnings.length > 0 && (
                <ul style={warningsListStyle}>
                  {doc.warnings.map((warning, i) => (
                    <li key={i}>{warning}</li>
                  ))}
                </ul>
              )}

              {doc.status === "failed" && doc.error_message && (
                <div style={{ fontSize: "0.8rem", color: "#b91c1c" }}>{doc.error_message}</div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

const sectionStyle = {
  width: "22rem",
  flexShrink: 0,
};

const cardStyle = {
  display: "flex",
  flexDirection: "column" as const,
  gap: "0.35rem",
  padding: "0.75rem",
  borderRadius: "0.5rem",
  border: "1px solid #e2e8f0",
  background: "white",
};

const badgeStyle = {
  display: "inline-block",
  padding: "0.15rem 0.5rem",
  borderRadius: "999px",
  fontSize: "0.7rem",
  fontWeight: 600,
  whiteSpace: "nowrap" as const,
};

const currentBadgeStyle = { background: "#eef2ff", color: "#4338ca" };
const supersededBadgeStyle = { background: "#f1f5f9", color: "#64748b" };

const warningsListStyle = {
  margin: 0,
  paddingLeft: "1.1rem",
  fontSize: "0.78rem",
  color: "#b45309",
};
