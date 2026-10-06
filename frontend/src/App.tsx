import { useEffect, useState } from "react";
import {
  APPROVER_ROLES,
  CLASSIFIER_ROLES,
  USER_ROLES,
  getHealth,
  getUserRole,
  setUserRole,
} from "./api/client";
import type { UserRole } from "./api/client";
import type { CitationOut, DocumentOut } from "./api/types";
import Chat from "./components/Chat";
import ComparisonView from "./components/ComparisonView";
import DocumentList from "./components/DocumentList";
import SourceVerificationPanel from "./components/SourceVerificationPanel";

type ApiStatus = "checking" | "online" | "offline";
type View = "chat" | "compare";

// "Save as PDF" on the comparison (ID-HU-FE-003) uses the browser's print
// dialog: everything marked .no-print is left out of the printout.
const PRINT_CSS = `@media print {
  .no-print { display: none !important; }
  main { padding: 0 !important; }
}`;

export default function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [selectedCitation, setSelectedCitation] = useState<CitationOut | null>(null);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [role, setRole] = useState<UserRole>(getUserRole());
  // ID-HU-BE-015: a specific version the chat is scoped to (e.g. superseded, for audit).
  const [pinnedDocument, setPinnedDocument] = useState<DocumentOut | null>(null);
  const [view, setView] = useState<View>("chat");

  function changeRole(next: UserRole) {
    setUserRole(next);
    setRole(next);
    // The open source panel was resolved under the previous role's access.
    setSelectedCitation(null);
  }

  useEffect(() => {
    getHealth()
      .then(() => setApiStatus("online"))
      .catch(() => setApiStatus("offline"));
  }, []);

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <style>{PRINT_CSS}</style>
      <header
        className="no-print"
        style={{
          maxWidth: "72rem",
          margin: "0 auto 1.5rem",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <div style={{ display: "flex", gap: "1.25rem", alignItems: "baseline" }}>
          <h1 style={{ fontSize: "1.4rem", margin: 0 }}>FinDoc Retriever</h1>
          <nav aria-label="Views" style={{ display: "flex", gap: "0.25rem" }}>
            {(["chat", "compare"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                aria-pressed={view === v}
                style={tabStyle(view === v)}
              >
                {v === "chat" ? "Chat" : "Compare"}
              </button>
            ))}
          </nav>
        </div>
        <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
          <label style={{ fontSize: "0.85rem", color: "#334155", display: "flex", gap: "0.4rem", alignItems: "center" }}>
            Role
            <select
              value={role}
              onChange={(e) => changeRole(e.target.value as UserRole)}
              aria-label="User role"
              style={roleSelectStyle}
            >
              {USER_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => setDocumentsOpen(true)} style={documentsButtonStyle}>
            Documents
          </button>
          <span style={{ fontSize: "0.8rem", color: apiStatus === "online" ? "#16a34a" : "#b91c1c" }}>
            API: {apiStatus}
          </span>
        </div>
      </header>
      <div style={{ maxWidth: "72rem", margin: "0 auto", display: "flex", gap: "1.5rem", alignItems: "flex-start" }}>
        {/* Both views stay mounted so switching tabs keeps the conversation. */}
        <div className="no-print" hidden={view !== "chat"} style={{ flex: 1, minWidth: 0 }}>
          <Chat
            onCitationClick={setSelectedCitation}
            pinnedDocument={pinnedDocument}
            onClearPin={() => setPinnedDocument(null)}
          />
        </div>
        <div hidden={view !== "compare"} style={{ flex: 1, minWidth: 0 }}>
          <ComparisonView onCitationClick={setSelectedCitation} active={view === "compare"} />
        </div>
        {selectedCitation && (
          <SourceVerificationPanel
            citation={selectedCitation}
            onClose={() => setSelectedCitation(null)}
          />
        )}
      </div>
      <DocumentList
        open={documentsOpen}
        onClose={() => setDocumentsOpen(false)}
        canClassify={CLASSIFIER_ROLES.includes(role)}
        canApprove={APPROVER_ROLES.includes(role)}
        onAskAboutVersion={(document) => {
          setPinnedDocument(document);
          setDocumentsOpen(false);
          setView("chat");
        }}
      />
    </main>
  );
}

function tabStyle(active: boolean) {
  return {
    padding: "0.3rem 0.75rem",
    borderRadius: "999px",
    border: active ? "1px solid #4f46e5" : "1px solid transparent",
    background: active ? "#eef2ff" : "transparent",
    color: active ? "#3730a3" : "#475569",
    cursor: "pointer",
    fontSize: "0.85rem",
    fontWeight: active ? 600 : 400,
  };
}

const roleSelectStyle = {
  padding: "0.3rem 0.4rem",
  borderRadius: "0.4rem",
  border: "1px solid #cbd5e1",
  fontSize: "0.85rem",
};

const documentsButtonStyle = {
  padding: "0.4rem 0.8rem",
  borderRadius: "0.5rem",
  border: "1px solid #4f46e5",
  background: "white",
  color: "#4f46e5",
  cursor: "pointer",
  fontSize: "0.85rem",
};
