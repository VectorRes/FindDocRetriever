import { useState } from "react";
import type { FormEvent } from "react";

interface ChatInputProps {
  disabled: boolean;
  onSubmit: (question: string) => void;
}

export default function ChatInput({ disabled, onSubmit }: ChatInputProps) {
  const [value, setValue] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const question = value.trim();
    if (!question || disabled) return;
    onSubmit(question);
    setValue("");
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", gap: "0.5rem" }}>
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Ask a question about your financial documents..."
        disabled={disabled}
        style={{
          flex: 1,
          padding: "0.6rem 0.8rem",
          borderRadius: "0.5rem",
          border: "1px solid #cbd5e1",
          fontSize: "0.95rem",
        }}
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        style={{
          padding: "0.6rem 1.1rem",
          borderRadius: "0.5rem",
          border: "none",
          background: disabled ? "#94a3b8" : "#4f46e5",
          color: "white",
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        {disabled ? "Asking..." : "Ask"}
      </button>
    </form>
  );
}
