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

  // The backend appends the restriction notice as an uncited statement; show it
  // as a banner instead so it isn't mistaken for a document-backed fact.
  const statements = answer.statements.filter((statement) => !statement.notice);
  const restrictionBanner = answer.restriction_notice ? (
    <div style={{ ...bannerStyle("#fef3c7", "#92400e"), marginBottom: "0.5rem" }}>
      🔒 {answer.restriction_notice}
    </div>
  ) : null;

  if (statements.length === 0 && restrictionBanner) {
    return restrictionBanner;
  }

  if (!answer.grounded || statements.length === 0) {
    return (
      <div style={bannerStyle("#fef2f2", "#b91c1c")}>
        I couldn't find a reliable answer to that in the indexed documents.
        Consider escalating this question to the relevant subject matter expert.
      </div>
    );
  }

  // ID-HU-BE-015: say which version(s) the answer is based on, and flag
  // loudly when that isn't the current version (an explicit audit question).
  const versionsUsed = answer.versions_used ?? [];
  const nonCurrent = versionsUsed.filter((version) => !version.is_current);

  return (
    <div>
      {restrictionBanner}
      {nonCurrent.map((version) => (
        <div key={version.document_id} style={{ ...bannerStyle("#fff7ed", "#c2410c"), marginBottom: "0.5rem" }}>
          This answer uses <strong>{version.filename}</strong> (v{version.version_number}), which is not the
          current version
          {version.current_version_filename && (
            <>
              {" "}— the current one is <strong>{version.current_version_filename}</strong>
            </>
          )}
          .
        </div>
      ))}
      {answer.has_conflicts && (
        <div style={{ ...bannerStyle("#fff7ed", "#c2410c"), marginBottom: "0.5rem" }}>
          Sources disagree on this — both values are shown below with their own citations.
        </div>
      )}
      {statements.map((statement, i) => (
        <p key={i} style={{ margin: "0.35rem 0" }}>
          {statement.text}
          {statement.citations.map((citation, j) => (
            <CitationBadge key={j} citation={citation} onClick={onCitationClick} />
          ))}
        </p>
      ))}
      {versionsUsed.length > 0 && (
        <div style={{ marginTop: "0.4rem", fontSize: "0.78rem", color: "#64748b" }}>
          Based on:{" "}
          {versionsUsed
            .map((v) => `${v.filename} (v${v.version_number}, ${v.approval_status}${v.is_current ? ", current" : ""})`)
            .join("; ")}
        </div>
      )}
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
