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
  period_coverage?: string | null;
  column_header?: string | null;
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

// --- ID-HU-BE-010: financial ratios ---

export type RatioCategory = "liquidity" | "solvency" | "profitability" | "efficiency";

export interface RatioRequest {
  // Statements to calculate from; each input is looked up across all of them.
  document_ids: string[];
  period?: string | null;
  ratios?: string[] | null;
  categories?: RatioCategory[] | null;
}

export interface RatioInput {
  key: string;
  name: string;
  statement: string;
  found: boolean;
  value: number | null;
  value_text: string | null;
  unit_scale: string;
  currency: string | null;
  period: string | null;
  label: string | null;
  entity?: string | null;
  // "three_months", "nine_months", "point_in_time"... and the column it was read from.
  period_coverage?: string | null;
  column_header?: string | null;
  document_id: string | null;
  document_filename: string | null;
  citation: CitationOut | null;
  confidence: string;
  // The label isn't a standard name for this input: the analyst should confirm it.
  needs_review: boolean;
  reason: string | null;
  // Covers a different length of time than the requested period (likely the wrong column).
  coverage_mismatch?: boolean;
  // Not found because the model service failed, not because it's missing.
  error?: boolean;
}

export interface Ratio {
  key: string;
  name: string;
  category: RatioCategory;
  formula: string;
  unit: "percent" | "times"; // percent: value is a fraction (0.25 = 25%)
  description: string;
  status: "calculated" | "needs_review" | "not_calculable";
  value: number | null;
  inputs: RatioInput[];
  citations: CitationOut[];
  cross_document: boolean;
  currency: string | null;
  period: string | null;
  reasons: string[];
  // ID-HU-BE-011: inputs not found, by name, when status is "not_calculable".
  missing_inputs: string[];
}

export interface RatioReport {
  period: string | null;
  document_ids: string[];
  ratios: Ratio[];
  line_items: RatioInput[];
  notes: string[];
}

// ID-HU-BE-014: one access attempt in the audit trail.
export interface AuditLog {
  id: string;
  occurred_at: string;
  user_id: string;
  roles: string[];
  // retrieve | lookup | extract | resolve | view_cell | view_file | audit_query
  action: string;
  // Where it came from: chat, query, ratios, compare, resolve, cells, file, audit…
  resource: string;
  document_id: string | null;
  document_filename: string | null;
  chunk_id: string | null;
  confidentiality_tag: string;
  authorized: boolean;
  is_insider: boolean;
  question: string;
  reason: string | null;
  request_id: string | null;
}

export interface AuditLogPage {
  total: number;
  limit: number;
  offset: number;
  items: AuditLog[];
}

export interface AuditFilters {
  user_id?: string;
  document?: string;
  from?: string; // ISO date-time
  to?: string;
  outcome?: "all" | "granted" | "denied";
  tier?: string;
  insider_only?: boolean;
  resource?: string;
}

// ID-HU-FE-004: key financial figures and ratios dashboard.
export type Statement = "income_statement" | "balance_sheet";

export interface CatalogDocument {
  id: string;
  filename: string;
  statements: Statement[]; // empty = couldn't tell
  periods: string[]; // empty = couldn't tell
}

export interface CatalogEntity {
  entity: string;
  key: string;
  periods: string[]; // chronological
  documents: CatalogDocument[];
}

export interface DashboardCatalog {
  entities: CatalogEntity[];
}

export interface Driver {
  name: string;
  previous: number | null;
  current: number | null;
  unit: "percent" | "times" | "amount";
  change: number | null; // relative: 0.5 = +50%
  note: string | null; // "leverage", "profitability"… for DuPont components
}

export interface Outlier {
  previous_period: string | null;
  previous_value: number | null;
  change: number | null;
  absolute_change: number | null;
  method: "period_change" | "robust_z";
  message: string;
  drivers: Driver[];
}

// not_calculable: can't be calculated for the period (ID-HU-BE-011: see
// missing_inputs); not_calculated: has documents, not refreshed yet.
export type PointStatus = "calculated" | "needs_review" | "not_calculable" | "not_calculated" | "restricted";

export interface DashboardPoint {
  period: string;
  status: PointStatus;
  value: number | null;
  currency: string | null;
  inputs: RatioInput[];
  citations: CitationOut[];
  cross_document: boolean;
  reasons: string[];
  missing_statements: Statement[];
  missing_inputs: string[];
  stale: boolean;
  outlier: Outlier | null;
}

export interface DashboardRatio {
  key: string;
  name: string;
  category: RatioCategory;
  formula: string;
  unit: "percent" | "times";
  description: string;
  points: DashboardPoint[]; // one per period, chronological
}

export interface DashboardPeriod {
  period: string;
  calculated: boolean;
  // No document covers this period: a gap in the entity's history.
  gap: boolean;
  stale: boolean;
  restricted: boolean;
  computed_at: string | null;
  computed_by: string | null;
  documents: { id: string; filename: string }[];
  notes: string[];
}

export interface Dashboard {
  entity: string;
  entity_key: string;
  periods: DashboardPeriod[];
  ratios: DashboardRatio[];
  notes: string[];
}
