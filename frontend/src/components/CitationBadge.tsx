import type { CitationOut } from "../api/types";

function describeLocation(citation: CitationOut): string {
  const parts = [citation.document_filename];
  if (citation.sheet_name) parts.push(`sheet "${citation.sheet_name}"`);
  if (citation.cell_range) parts.push(citation.cell_range);
  if (citation.page_number != null) parts.push(`p. ${citation.page_number}`);
  return parts.join(", ");
}

interface CitationBadgeProps {
  citation: CitationOut;
  onClick: (citation: CitationOut) => void;
}

export default function CitationBadge({ citation, onClick }: CitationBadgeProps) {
  return (
    <button
      type="button"
      onClick={() => onClick(citation)}
      title={describeLocation(citation)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        marginLeft: "0.35rem",
        padding: "0.05rem 0.45rem",
        fontSize: "0.75rem",
        borderRadius: "999px",
        border: "1px solid #94a3b8",
        background: "#eef2ff",
        color: "#3730a3",
        cursor: "pointer",
      }}
    >
      {citation.document_filename}
      {citation.sheet_name ? ` · ${citation.sheet_name}` : ""}
      {citation.cell_range ? ` ${citation.cell_range}` : ""}
      {citation.page_number != null ? ` p.${citation.page_number}` : ""}
    </button>
  );
}
