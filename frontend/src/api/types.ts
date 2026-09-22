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
}

export interface QueryRequest {
  question: string;
  top_k?: number;
  session_id?: string | null;
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
