export interface CitationOut {
  source_id: string;
  document_id: string;
  document_filename: string;
  sheet_name: string | null;
  cell_range: string | null;
  page_number: number | null;
  reference_number: string | null;
  text: string;
}

export interface StatementOut {
  text: string;
  citations: CitationOut[];
  conflicting: boolean;
  // True for the backend's uncited "some information is restricted" notice.
  notice?: boolean;
}

export interface AnswerResponse {
  question: string;
  statements: StatementOut[];
  sources: CitationOut[];
  has_conflicts: boolean;
  grounded: boolean;
  session_id: string;
  needs_clarification: boolean;
  clarification_question: string | null;
  // Set when relevant sources exist that the current role can't access.
  restriction_notice?: string | null;
  // ID-HU-BE-015: which document version(s) the cited sources came from.
  versions_used?: VersionUsed[];
  // ID-HU-BE-009: choices found in the sources for an ambiguous question.
  clarification_options?: string[];
  // ID-HU-BE-009: null when there is no answer to rate.
  confidence?: Confidence | null;
  // ID-HU-BE-009: team to consult when there's no confident answer.
  escalation?: EscalationSuggestion | null;
}

export interface Confidence {
  level: "high" | "medium" | "low";
  score: number;
  reasons: string[];
}

export interface EscalationSuggestion {
  team: string;
  topic: string;
  reason: string;
}

export interface VersionUsed {
  document_id: string;
  filename: string;
  version_number: number;
  approval_status: string;
  is_current: boolean;
  // Set when the answer used a non-default (e.g. superseded) version.
  current_version_filename: string | null;
}

export interface QueryRequest {
  question: string;
  top_k?: number;
  session_id?: string | null;
  // Scope the question to one specific document version (ID-HU-BE-015).
  document_id?: string | null;
}

export interface DocumentOut {
  id: string;
  filename: string;
  doc_type: string;
  status: string;
  error_message: string | null;
  warnings: string[];
  uploaded_at: string;
  is_current: boolean;
  superseded_by_id: string | null;
  // ID-HU-BE-015 version control.
  version_group: string;
  version_number: number;
  approval_status: string; // "draft" | "approved"
  approved_at: string | null;
  confidentiality_tag: string;
  sheet_names: string[];
}

export interface DocumentListOut {
  documents: DocumentOut[];
}

export interface AccessDecision {
  allowed: boolean;
  reason: string | null;
}

export interface DocumentResolutionOut {
  document: DocumentOut;
  is_superseded: boolean;
  current_version: DocumentOut | null;
  access: AccessDecision;
}

export interface CellOut {
  sheet_name: string;
  address: string;
  value: string | null;
  formula: string | null;
  formula_references: string[];
}
