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

// --- ID-HU-FE-003: cross-document comparison ---

export interface CompareRequest {
  // First document is the baseline variances are measured against.
  document_ids: string[];
  metric: string;
  period?: string | null;
}

export interface ExchangeRate {
  from_currency: string;
  to_currency: string;
  rate: number; // 1 from_currency = rate to_currency
  rate_text: string;
  citation: CitationOut;
}

export interface ComparisonRow {
  document_id: string;
  filename: string;
  version_number: number;
  is_current: boolean;
  is_baseline: boolean;
  found: boolean;
  value: number | null;
  value_text: string | null;
  unit_scale: string;
  currency: string | null;
  period: string | null;
  label: string | null;
  citation: CitationOut | null;
  confidence: string;
  reason: string | null;
  comparable_value: number | null;
  converted: boolean;
  variance_abs: number | null;
  variance_pct: number | null;
  matches_baseline: boolean | null;
}

export interface Comparison {
  metric: string;
  period: string | null;
  rows: ComparisonRow[];
  // null: can't tell (fewer than two comparable values, or no documented exchange rate).
  reconciles: boolean | null;
  // Values for different periods: variance only, no reconciliation expected.
  across_periods: boolean;
  comparison_currency: string | null;
  exchange_rates: ExchangeRate[];
  notes: string[];
  escalation: EscalationSuggestion | null;
}
