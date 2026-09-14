import { useState } from "react";
import { ApiError, postQueryAnswer } from "../api/client";
import type { AnswerResponse, CitationOut } from "../api/types";
import AnswerMessage from "./AnswerMessage";
import ChatInput from "./ChatInput";

interface ConversationTurn {
  id: number;
  question: string;
  status: "loading" | "done" | "error";
  answer?: AnswerResponse;
  errorMessage?: string;
}

let nextTurnId = 1;

interface ChatProps {
  onCitationClick: (citation: CitationOut) => void;
}

export default function Chat({ onCitationClick }: ChatProps) {
  const [turns, setTurns] = useState<ConversationTurn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const isSending = turns.some((t) => t.status === "loading");

  async function handleSubmit(question: string) {
    const id = nextTurnId++;
    setTurns((prev) => [...prev, { id, question, status: "loading" }]);

    try {
      const answer = await postQueryAnswer({ question, session_id: sessionId });
      setSessionId(answer.session_id);
      setTurns((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "done", answer } : t))
      );
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.kind === "timeout"
            ? "The request took too long — please try again."
            : err.kind === "network"
              ? "Couldn't reach the FinDoc Retriever API. Check your connection and try again."
              : `Something went wrong (${err.status ?? "error"}).`
          : "Something went wrong.";
      setTurns((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "error", errorMessage: message } : t))
      );
    }
  }

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1rem" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        {turns.length === 0 && (
          <p style={{ color: "#64748b", fontSize: "0.9rem" }}>
            Ask a question about your financial documents to get started.
          </p>
        )}
        {turns.map((turn) => (
          <div key={turn.id} style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            <div style={questionBubbleStyle}>{turn.question}</div>
            {turn.status === "loading" && (
              <div style={{ color: "#64748b", fontSize: "0.9rem" }}>Thinking...</div>
            )}
            {turn.status === "error" && (
              <div style={{ color: "#b91c1c", fontSize: "0.9rem" }}>{turn.errorMessage}</div>
            )}
            {turn.status === "done" && turn.answer && (
              <AnswerMessage answer={turn.answer} onCitationClick={onCitationClick} />
            )}
          </div>
        ))}
      </div>

      <ChatInput disabled={isSending} onSubmit={handleSubmit} />
    </div>
  );
}

const questionBubbleStyle = {
  alignSelf: "flex-end" as const,
  background: "#4f46e5",
  color: "white",
  padding: "0.5rem 0.8rem",
  borderRadius: "0.75rem 0.75rem 0 0.75rem",
  maxWidth: "80%",
};
