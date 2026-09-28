import { useEffect, useState } from "react";
import { CLASSIFIER_ROLES, USER_ROLES, getHealth, getUserRole, setUserRole } from "./api/client";
import type { UserRole } from "./api/client";
import type { CitationOut } from "./api/types";
import Chat from "./components/Chat";
import DocumentList from "./components/DocumentList";
import SourceVerificationPanel from "./components/SourceVerificationPanel";

type ApiStatus = "checking" | "online" | "offline";

export default function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [selectedCitation, setSelectedCitation] = useState<CitationOut | null>(null);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [role, setRole] = useState<UserRole>(getUserRole());

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
      <header
        style={{
          maxWidth: "72rem",
          margin: "0 auto 1.5rem",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <h1 style={{ fontSize: "1.4rem", margin: 0 }}>FinDoc Retriever</h1>
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
        <Chat onCitationClick={setSelectedCitation} />
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
      />
    </main>
  );
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
