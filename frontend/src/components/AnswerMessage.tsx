import type { CSSProperties } from "react";
import { SOURCE_TIER_LABELS } from "../api/types";
import type { AnswerResponse, CitationOut, SourceNotice } from "../api/types";
import CitationBadge from "./CitationBadge";

interface AnswerMessageProps {
  answer: AnswerResponse;
  onCitationClick: (citation: CitationOut) => void;
  // ID-HU-BE-009: send one of the clarification options as the next question.
  onClarify?: (reply: string) => void;
}

// ID-HU-BE-016: how each kind of source notice is shown.
const SOURCE_NOTICE_STYLES: Record<SourceNotice["kind"], { title: string; background: string; color: string }> = {
  draft: { title: "Draft source", background: "#fef3c7", color: "#92400e" },
  lower_tier: { title: "Lower-tier source", background: "#fff7ed", color: "#c2410c" },
  higher_tier_preferred: { title: "Higher-tier source used", background: "#f1f5f9", color: "#334155" },
};

const CONFIDENCE_STYLES = {
  high: { background: "#f0fdf4", color: "#15803d" },
  medium: { background: "#fffbeb", color: "#b45309" },
  low: { background: "#fef2f2", color: "#b91c1c" },
};

export default function AnswerMessage({ answer, onCitationClick, onClarify }: AnswerMessageProps) {
  if (answer.needs_clarification) {
    const options = answer.clarification_options ?? [];
    return (
      <div style={bannerStyle("#fffbeb", "#b45309")}>
        {answer.clarification_question ??
          "Could you clarify which entity and/or period you mean?"}
        {options.length > 0 && onClarify && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginTop: "0.5rem" }}>
            {options.map((option) => (
              <button key={option} type="button" onClick={() => onClarify(option)} style={optionButtonStyle}>
                {option}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  const escalationLine = answer.escalation ? (
    <div style={{ marginTop: "0.3rem" }}>
      Suggested team to consult: <strong>{answer.escalation.team}</strong>
    </div>
  ) : null;

  // The backend appends the restriction notice as an uncited statement; show it
  // as a banner instead so it isn't mistaken for a document-backed fact.
  const statements = answer.statements.filter((statement) => !statement.notice);
  const restrictionBanner = answer.restriction_notice ? (
    <div style={{ ...bannerStyle("#fef3c7", "#92400e"), marginBottom: "0.5rem" }}>
      🔒 {answer.restriction_notice}
    </div>
  ) : null;

  if (!answer.grounded || statements.length === 0) {
    // ID-HU-BE-009: say plainly there's no confident answer — alongside the
    // restriction notice when restricted content was also excluded.
    return (
      <div>
        {restrictionBanner}
        <div style={bannerStyle("#fef2f2", "#b91c1c")}>
          I couldn't find a reliable answer to that in the documents you can access, so no figure is shown.
          {escalationLine ?? " Consider escalating this question to the relevant subject matter expert."}
        </div>
      </div>
    );
  }

  // ID-HU-BE-015: say which version(s) the answer is based on, and flag
  // loudly when that isn't the current version (an explicit audit question).
  const versionsUsed = answer.versions_used ?? [];
  const nonCurrent = versionsUsed.filter((version) => !version.is_current);
  const confidence = answer.confidence;

  return (
    <div>
      {restrictionBanner}
      {confidence?.level === "low" && (
        <div style={{ ...bannerStyle("#fef2f2", "#b91c1c"), marginBottom: "0.5rem" }}>
          <strong>Low confidence.</strong> Verify this answer before relying on it.
          {confidence.reasons.length > 0 && (
            <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>
              {confidence.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {escalationLine}
        </div>
      )}
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
      {(answer.source_notices ?? []).map((notice) => {
        const style = SOURCE_NOTICE_STYLES[notice.kind];
        return (
          <div
            key={`${notice.kind}-${notice.document_filenames.join(",")}`}
            role="note"
            aria-label={style.title}
            style={{ ...bannerStyle(style.background, style.color), marginBottom: "0.5rem" }}
          >
            <strong>{style.title}.</strong> {notice.message}
          </div>
        );
      })}
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
      {confidence && (
        <div style={{ marginTop: "0.4rem" }}>
          <span
            style={{ ...confidenceBadgeStyle, ...CONFIDENCE_STYLES[confidence.level] }}
            title={confidence.reasons.join("; ") || "Every figure was found in its cited source."}
          >
            {confidence.level} confidence
          </span>
        </div>
      )}
      {versionsUsed.length > 0 && (
        <div style={{ marginTop: "0.4rem", fontSize: "0.78rem", color: "#64748b" }}>
          Based on:{" "}
          {versionsUsed
            .map(
              (v) =>
                `${v.filename} (${v.source_tier ? `${SOURCE_TIER_LABELS[v.source_tier]}, ` : ""}v${v.version_number}, ` +
                `${v.approval_status}${v.is_current ? ", current" : ""})`,
            )
            .join("; ")}
        </div>
      )}
    </div>
  );
}

const optionButtonStyle: CSSProperties = {
  padding: "0.25rem 0.65rem",
  borderRadius: "999px",
  border: "1px solid #b45309",
  background: "white",
  color: "#b45309",
  cursor: "pointer",
  fontSize: "0.8rem",
};

const confidenceBadgeStyle: CSSProperties = {
  display: "inline-block",
  padding: "0.1rem 0.5rem",
  borderRadius: "999px",
  fontSize: "0.7rem",
  fontWeight: 600,
};

function bannerStyle(background: string, color: string): CSSProperties {
  return {
    background,
    color,
    padding: "0.6rem 0.8rem",
    borderRadius: "0.5rem",
    fontSize: "0.9rem",
  };
}
