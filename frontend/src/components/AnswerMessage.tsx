import type { CSSProperties } from "react";
import type { AnswerResponse, CitationOut } from "../api/types";
import CitationBadge from "./CitationBadge";

interface AnswerMessageProps {
  answer: AnswerResponse;
  onCitationClick: (citation: CitationOut) => void;
}

export default function AnswerMessage({ answer, onCitationClick }: AnswerMessageProps) {
  if (answer.needs_clarification) {
    return (
      <div style={bannerStyle("#fffbeb", "#b45309")}>
        {answer.clarification_question ??
          "Could you clarify which entity and/or period you mean?"}
      </div>
    );
  }

  if (!answer.grounded || answer.statements.length === 0) {
    return (
      <div style={bannerStyle("#fef2f2", "#b91c1c")}>
        I couldn't find a reliable answer to that in the indexed documents.
        Consider escalating this question to the relevant subject matter expert.
      </div>
    );
  }

  return (
    <div>
      {answer.has_conflicts && (
        <div style={{ ...bannerStyle("#fff7ed", "#c2410c"), marginBottom: "0.5rem" }}>
          Sources disagree on this — both values are shown below with their own citations.
        </div>
      )}
      {answer.statements.map((statement, i) => (
        <p key={i} style={{ margin: "0.35rem 0" }}>
          {statement.text}
          {statement.citations.map((citation, j) => (
            <CitationBadge key={j} citation={citation} onClick={onCitationClick} />
          ))}
        </p>
      ))}
    </div>
  );
}

function bannerStyle(background: string, color: string): CSSProperties {
  return {
    background,
    color,
    padding: "0.6rem 0.8rem",
    borderRadius: "0.5rem",
    fontSize: "0.9rem",
  };
}
