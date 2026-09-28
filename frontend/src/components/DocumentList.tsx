import { useEffect, useState } from "react";
import {
  ApiError,
  approveDocument,
  deleteDocument,
  listDocuments,
  setDocumentConfidentiality,
} from "../api/client";
import type { DocumentOut } from "../api/types";
import DocumentUpload from "./DocumentUpload";

const STATUS_STYLES: Record<string, { background: string; color: string }> = {
  queued: { background: "#f1f5f9", color: "#475569" },
  processing: { background: "#eff6ff", color: "#1d4ed8" },
  ready: { background: "#f0fdf4", color: "#15803d" },
  failed: { background: "#fef2f2", color: "#b91c1c" },
};

// Tags accepted by the backend's normalize_tag (app/retrieval/access.py).
const CONFIDENTIALITY_OPTIONS = [
  "public",
  "internal",
  "restricted",
  "restricted:compensation",
  "restricted:related-parties",
  "restricted:legal",
];

// ID-HU-BE-015: where a version stands in its group. A newer draft that
// hasn't been approved yet is neither current nor superseded.
function versionState(doc: DocumentOut): { label: string; style: { background: string; color: string } } | null {
  if (doc.is_current) return { label: "current version", style: currentBadgeStyle };
  if (doc.superseded_by_id) return { label: "superseded", style: supersededBadgeStyle };
  if (doc.status === "ready" && doc.approval_status === "draft") {
    return { label: "pending approval", style: pendingBadgeStyle };
  }
  return null;
}

function confidentialityStyle(tag: string): { background: string; color: string } {
  if (tag === "public") return { background: "#f0fdf4", color: "#15803d" };
  if (tag === "internal") return { background: "#eff6ff", color: "#1d4ed8" };
  return { background: "#fef3c7", color: "#92400e" };
}

interface DocumentListProps {
  open: boolean;
  onClose: () => void;
  // Whether the current role may reclassify documents (see CLASSIFIER_ROLES).
  canClassify?: boolean;
  // Whether the current role may approve versions (see APPROVER_ROLES).
  canApprove?: boolean;
  // Scope the chat to one specific version, e.g. a superseded one for audit.
  onAskAboutVersion?: (document: DocumentOut) => void;
}

export default function DocumentList({
  open,
  onClose,
  canClassify = false,
  canApprove = false,
  onAskAboutVersion,
}: DocumentListProps) {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [status, setStatus] = useState<"loading" | "done" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [classifyingId, setClassifyingId] = useState<string | null>(null);
  const [classifyError, setClassifyError] = useState<{ id: string; message: string } | null>(null);
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<{ id: string; message: string } | null>(null);

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

  useEffect(() => {
    if (open) refresh();
  }, [open]);

  async function confirmDelete(documentId: string) {
    setDeletingId(documentId);
    try {
      await deleteDocument(documentId);
      setPendingDeleteId(null);
      refresh();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Could not delete this document.");
    } finally {
      setDeletingId(null);
    }
  }

  async function changeConfidentiality(documentId: string, tag: string) {
    setClassifyingId(documentId);
    setClassifyError(null);
    try {
      const updated = await setDocumentConfidentiality(documentId, tag);
      setDocuments((prev) => prev.map((doc) => (doc.id === documentId ? updated : doc)));
    } catch (err) {
      setClassifyError({
        id: documentId,
        message: err instanceof ApiError ? err.message : "Could not update the classification.",
      });
    } finally {
      setClassifyingId(null);
    }
  }

  async function approve(documentId: string) {
    setApprovingId(documentId);
    setApproveError(null);
    try {
      await approveDocument(documentId);
      // Approval can change which version of the group is current, so reload
      // the whole list rather than just this card.
      refresh();
    } catch (err) {
      setApproveError({
        id: documentId,
        message: err instanceof ApiError ? err.message : "Could not approve this version.",
      });
    } finally {
      setApprovingId(null);
    }
  }

  function byId(id: string | null): DocumentOut | undefined {
    return id ? documents.find((doc) => doc.id === id) : undefined;
  }

  if (!open) return null;

  return (
    <>
      <div onClick={onClose} style={backdropStyle} />
      <aside style={drawerStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
          <h2 style={{ fontSize: "1rem", margin: 0 }}>Documents</h2>
          <button type="button" onClick={onClose} style={closeButtonStyle} aria-label="Close documents panel">
            ×
          </button>
        </div>

        <DocumentUpload onUploaded={refresh} />
        <p style={{ margin: "0.4rem 0 0", fontSize: "0.75rem", color: "#64748b" }}>
          New uploads are <strong>restricted</strong> by default and won't be used to answer
          questions for roles without access until they're reclassified.
        </p>

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
            const isPendingDelete = pendingDeleteId === doc.id;
            const version = versionState(doc);

            return (
              <div key={doc.id} style={cardStyle}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.5rem" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem", wordBreak: "break-all" }}>{doc.filename}</span>
                  <span style={{ ...badgeStyle, ...statusStyle }}>{doc.status}</span>
                </div>

                <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                  <span style={{ ...badgeStyle, ...versionNumberBadgeStyle }}>v{doc.version_number}</span>
                  {version && <span style={{ ...badgeStyle, ...version.style }}>{version.label}</span>}
                  {doc.approval_status === "approved" && (
                    <span style={{ ...badgeStyle, ...approvedBadgeStyle }}>approved</span>
                  )}
                  <span style={{ ...badgeStyle, ...confidentialityStyle(doc.confidentiality_tag) }}>
                    {doc.confidentiality_tag}
                  </span>
                </div>

                {canClassify && (
                  <label style={{ fontSize: "0.8rem", color: "#334155", display: "flex", gap: "0.4rem", alignItems: "center" }}>
                    Classification
                    <select
                      value={doc.confidentiality_tag}
                      onChange={(e) => changeConfidentiality(doc.id, e.target.value)}
                      disabled={classifyingId === doc.id}
                      aria-label={`Classification for ${doc.filename}`}
                      style={selectStyle}
                    >
                      {(CONFIDENTIALITY_OPTIONS.includes(doc.confidentiality_tag)
                        ? CONFIDENTIALITY_OPTIONS
                        : [doc.confidentiality_tag, ...CONFIDENTIALITY_OPTIONS]
                      ).map((tag) => (
                        <option key={tag} value={tag}>
                          {tag}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {classifyError?.id === doc.id && (
                  <div style={{ fontSize: "0.8rem", color: "#b91c1c" }}>{classifyError.message}</div>
                )}

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
                  <details>
                    <summary style={warningsSummaryStyle}>
                      {doc.warnings.length} parsing warning{doc.warnings.length > 1 ? "s" : ""}
                    </summary>
                    <ul style={warningsListStyle}>
                      {doc.warnings.map((warning, i) => (
                        <li key={i}>{warning}</li>
                      ))}
                    </ul>
                  </details>
                )}

                {doc.status === "failed" && doc.error_message && (
                  <div style={{ fontSize: "0.8rem", color: "#b91c1c" }}>{doc.error_message}</div>
                )}

                {approveError?.id === doc.id && (
                  <div style={{ fontSize: "0.8rem", color: "#b91c1c" }}>{approveError.message}</div>
                )}

                <div style={{ marginTop: "0.15rem", display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                  {canApprove && doc.status === "ready" && doc.approval_status === "draft" && !isPendingDelete && (
                    <button
                      type="button"
                      onClick={() => approve(doc.id)}
                      disabled={approvingId === doc.id}
                      style={approveButtonStyle}
                    >
                      {approvingId === doc.id ? "Approving..." : "Approve version"}
                    </button>
                  )}
                  {onAskAboutVersion && doc.status === "ready" && !isPendingDelete && (
                    <button type="button" onClick={() => onAskAboutVersion(doc)} style={askButtonStyle}>
                      Ask about this version
                    </button>
                  )}
                  {isPendingDelete ? (
                    <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                      <span style={{ fontSize: "0.8rem", color: "#b91c1c" }}>Delete this document?</span>
                      <button
                        type="button"
                        onClick={() => confirmDelete(doc.id)}
                        disabled={deletingId === doc.id}
                        style={confirmDeleteButtonStyle}
                      >
                        {deletingId === doc.id ? "Deleting..." : "Yes, delete"}
                      </button>
                      <button type="button" onClick={() => setPendingDeleteId(null)} style={cancelButtonStyle}>
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button type="button" onClick={() => setPendingDeleteId(doc.id)} style={deleteButtonStyle}>
                      Delete
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}

const backdropStyle = {
  position: "fixed" as const,
  inset: 0,
  background: "rgba(15, 23, 42, 0.25)",
  zIndex: 40,
};

const drawerStyle = {
  position: "fixed" as const,
  top: 0,
  right: 0,
  height: "100vh",
  width: "24rem",
  maxWidth: "90vw",
  background: "white",
  borderLeft: "1px solid #e2e8f0",
  boxShadow: "-4px 0 16px rgba(15, 23, 42, 0.12)",
  padding: "1rem",
  overflowY: "auto" as const,
  zIndex: 50,
};

const closeButtonStyle = {
  border: "none",
  background: "transparent",
  fontSize: "1.3rem",
  lineHeight: 1,
  cursor: "pointer",
  color: "#64748b",
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
const pendingBadgeStyle = { background: "#fff7ed", color: "#c2410c" };
const approvedBadgeStyle = { background: "#f0fdf4", color: "#15803d" };
const versionNumberBadgeStyle = { background: "#f8fafc", color: "#334155", border: "1px solid #e2e8f0" };

const approveButtonStyle = {
  padding: "0.25rem 0.6rem",
  borderRadius: "0.4rem",
  border: "1px solid #15803d",
  background: "white",
  color: "#15803d",
  cursor: "pointer",
  fontSize: "0.78rem",
};

const askButtonStyle = {
  padding: "0.25rem 0.6rem",
  borderRadius: "0.4rem",
  border: "1px solid #4f46e5",
  background: "white",
  color: "#4f46e5",
  cursor: "pointer",
  fontSize: "0.78rem",
};

const warningsSummaryStyle = {
  cursor: "pointer",
  fontSize: "0.78rem",
  color: "#b45309",
};

const warningsListStyle = {
  margin: "0.3rem 0 0",
  paddingLeft: "1.1rem",
  fontSize: "0.78rem",
  color: "#b45309",
};

const selectStyle = {
  padding: "0.15rem 0.3rem",
  borderRadius: "0.35rem",
  border: "1px solid #cbd5e1",
  fontSize: "0.78rem",
};

const deleteButtonStyle = {
  padding: "0.25rem 0.6rem",
  borderRadius: "0.4rem",
  border: "1px solid #e2e8f0",
  background: "white",
  color: "#b91c1c",
  cursor: "pointer",
  fontSize: "0.78rem",
};

const confirmDeleteButtonStyle = {
  padding: "0.25rem 0.6rem",
  borderRadius: "0.4rem",
  border: "1px solid #b91c1c",
  background: "#b91c1c",
  color: "white",
  cursor: "pointer",
  fontSize: "0.78rem",
};

const cancelButtonStyle = {
  padding: "0.25rem 0.6rem",
  borderRadius: "0.4rem",
  border: "1px solid #cbd5e1",
  background: "white",
  color: "#475569",
  cursor: "pointer",
  fontSize: "0.78rem",
};
