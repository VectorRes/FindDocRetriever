import { useEffect, useState } from "react";
import { getHealth } from "./api/client";
import type { CitationOut } from "./api/types";
import Chat from "./components/Chat";
import SourceVerificationPanel from "./components/SourceVerificationPanel";

type ApiStatus = "checking" | "online" | "offline";

export default function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [selectedCitation, setSelectedCitation] = useState<CitationOut | null>(null);

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
        <span style={{ fontSize: "0.8rem", color: apiStatus === "online" ? "#16a34a" : "#b91c1c" }}>
          API: {apiStatus}
        </span>
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
    </main>
  );
}
