from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    doc_type: str
    status: str
    error_message: str | None
    warnings: list[str]
    uploaded_at: datetime
    is_current: bool
    superseded_by_id: UUID | None
    # ID-HU-BE-015 version control: see app/versioning.py.
    version_group: str
    version_number: int
    approval_status: str
    approved_at: datetime | None = None
    # ID-HU-BE-016: "erp_epm" | "official_report" | "internal_report".
    source_tier: str = "internal_report"
    confidentiality_tag: str
    # Sheet names detected during Excel ingestion (ID-HU-FE-005's "parsing
    # summary"); always empty for PDFs.
    sheet_names: list[str] = []


class DocumentListOut(BaseModel):
    documents: list[DocumentOut]


class QueryRequest(BaseModel):
    question: str
    top_k: int | None = None
    # Omit on the first question of a conversation; pass back the session_id
    # from the previous AnswerResponse to keep follow-up context (ID-HU-FE-001).
    session_id: UUID | None = None
    # ID-HU-BE-015: scope the question to one specific document version (e.g.
    # a superseded one, for audit). Omit to use each document's default version.
    document_id: UUID | None = None


class RetrievedChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    document_filename: str
    sheet_name: str | None
    cell_range: str | None
    page_number: int | None
    text: str
    content_type: str = "text"
    reference_number: str | None = None
    extraction_method: str = "native"
    confidence: float | None = None
    confidence_label: str | None = None
    # ID-HU-BE-007: relevance to the question (cosine similarity, higher is better).
    score: float | None = None


class PdfTableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    page_number: int
    table_index: int
    headers: list
    rows: list
    confidence: float
    confidence_label: str
    raw_text: str


class PdfReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    page_number: int
    reference_type: str
    reference_number: str
    title: str | None
    text: str


class PdfStructureResponse(BaseModel):
    document_id: UUID
    tables: list[PdfTableOut]
    references: list[PdfReferenceOut]


class QueryResponse(BaseModel):
    question: str
    results: list[RetrievedChunkOut]


class CitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    document_id: str
    document_filename: str
    sheet_name: str | None
    cell_range: str | None
    page_number: int | None
    reference_number: str | None
    text: str


class VersionUsedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    filename: str
    version_number: int
    approval_status: str
    is_current: bool
    current_version_filename: str | None = None
    # ID-HU-BE-016: the document's place in the source hierarchy.
    source_tier: str = "internal_report"


class SourceNoticeOut(BaseModel):
    """ID-HU-BE-016: what the analyst should know about the answer's sources."""

    model_config = ConfigDict(from_attributes=True)

    # "draft": a cited document isn't approved; "lower_tier": a cited source
    # isn't ERP/EPM (no higher-tier source covered it); "higher_tier_preferred":
    # a lower-tier source also covered the figure but wasn't used as primary.
    kind: str
    message: str
    document_filenames: list[str] = []


class StatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    citations: list[CitationOut]
    conflicting: bool
    # True for the uncited restriction notice appended by the QA graph, so the
    # UI can render it as a notice rather than as a document-backed statement.
    notice: bool = False


class ConfidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    level: str  # "high" | "medium" | "low"
    score: float
    reasons: list[str] = []


class EscalationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    team: str
    topic: str
    reason: str


class AnswerResponse(BaseModel):
    """Response for ID-HU-BE-008: a grounded answer, every statement cited."""

    model_config = ConfigDict(from_attributes=True)

    question: str
    statements: list[StatementOut]
    sources: list[CitationOut]
    has_conflicts: bool
    # False when no statement could be safely grounded (e.g. no relevant sources
    # were retrieved, or every statement failed citation/figure verification):
    # the "no confident answer" response of ID-HU-BE-009.
    grounded: bool
    # Pass this back as session_id on the next QueryRequest to continue the
    # conversation with follow-up context (ID-HU-FE-001).
    session_id: UUID
    # ID-HU-BE-009: set instead of an answer when the question is ambiguous
    # (missing entity and/or period); options are the choices found in sources.
    needs_clarification: bool = False
    clarification_question: str | None = None
    clarification_options: list[str] = []
    # ID-HU-BE-009: how much to trust the answer (None when there is no answer).
    confidence: ConfidenceOut | None = None
    # ID-HU-BE-009: team to consult when there's no confident answer.
    escalation: EscalationOut | None = None
    restriction_notice: str | None = None
    # ID-HU-BE-015: which document version(s) the cited sources came from.
    versions_used: list[VersionUsedOut] = []
    # ID-HU-BE-016: draft and lower-tier source warnings, and higher-tier
    # sources preferred over lower-tier ones that also covered the figure.
    source_notices: list[SourceNoticeOut] = []


class CellOut(BaseModel):
    """Resolves a citation's sheet/cell to its live value and formula (ID-HU-FE-002)."""

    model_config = ConfigDict(from_attributes=True)

    sheet_name: str
    address: str
    value: str | None
    formula: str | None
    formula_references: list[str]


class AccessDecision(BaseModel):
    allowed: bool
    # Populated only when allowed is False — a message suitable for display to the analyst.
    reason: str | None = None


class DocumentResolutionOut(BaseModel):
    """What the Source Verification Panel needs before it can safely show a
    citation's source (ID-HU-FE-002): is this the current version, and is the
    viewer allowed to see it at all."""

    document: DocumentOut
    is_superseded: bool
    current_version: DocumentOut | None
    access: AccessDecision


class SupersedeRequest(BaseModel):
    new_document_id: UUID


class ConfidentialityRequest(BaseModel):
    tag: str


class SourceTierRequest(BaseModel):
    # ID-HU-BE-016: "erp_epm" | "official_report" | "internal_report".
    tier: str


# --- ID-HU-FE-003: cross-document comparison --------------------------------

class CompareRequest(BaseModel):
    # Two to six documents; the first one is the baseline variances are measured against.
    document_ids: list[UUID] = Field(min_length=2, max_length=6)
    metric: str = Field(min_length=1, max_length=200)
    # Optional: which period to take when a document holds several.
    period: str | None = None


class ExchangeRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_currency: str
    to_currency: str
    rate: float
    rate_text: str
    citation: CitationOut


class ComparisonRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    filename: str
    version_number: int
    is_current: bool
    is_baseline: bool
    found: bool
    value: float | None = None
    value_text: str | None = None
    unit_scale: str = "units"
    currency: str | None = None
    period: str | None = None
    label: str | None = None
    citation: CitationOut | None = None
    confidence: str = "low"
    reason: str | None = None
    period_coverage: str | None = None
    column_header: str | None = None
    comparable_value: float | None = None
    converted: bool = False
    variance_abs: float | None = None
    variance_pct: float | None = None
    matches_baseline: bool | None = None


class ComparisonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric: str
    period: str | None = None
    rows: list[ComparisonRowOut]
    reconciles: bool | None
    # Values for different periods: variance only, no reconciliation expected.
    across_periods: bool = False
    comparison_currency: str | None = None
    exchange_rates: list[ExchangeRateOut] = []
    notes: list[str] = []
    escalation: EscalationOut | None = None


# --- ID-HU-BE-010: financial ratio calculation -------------------------------

class RatioRequest(BaseModel):
    # The statements to calculate from (e.g. the income statement and the
    # balance sheet); inputs are looked up across all of them.
    document_ids: list[UUID] = Field(min_length=1, max_length=6)
    # Optional: which period to take when a document holds several.
    period: str | None = None
    # Optional subsets; omit both for every ratio in the library.
    ratios: list[str] | None = None
    categories: list[str] | None = None


class RatioInputOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    statement: str
    found: bool
    value: float | None = None
    value_text: str | None = None
    unit_scale: str = "units"
    currency: str | None = None
    period: str | None = None
    label: str | None = None
    entity: str | None = None
    period_coverage: str | None = None
    column_header: str | None = None
    document_id: UUID | None = None
    document_filename: str | None = None
    citation: CitationOut | None = None
    confidence: str = "low"
    needs_review: bool = False
    reason: str | None = None
    coverage_mismatch: bool = False
    error: bool = False


class RatioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    category: str
    formula: str
    unit: str  # "percent" (value is a fraction) | "times"
    description: str
    status: str  # "calculated" | "needs_review" | "not_calculable"
    value: float | None = None
    inputs: list[RatioInputOut]
    citations: list[CitationOut]
    cross_document: bool
    currency: str | None = None
    period: str | None = None
    reasons: list[str] = []
    # Inputs that weren't found, by name, when status is "not_calculable".
    missing_inputs: list[str] = []


class RatioReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: str | None = None
    document_ids: list[UUID]
    ratios: list[RatioOut]
    line_items: list[RatioInputOut]
    notes: list[str] = []


class RatioDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    category: str
    formula: str
    unit: str
    description: str
    inputs: list[str]


# --- ID-HU-BE-014: access audit trail -----------------------------------------

class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    user_id: str
    roles: list[str]
    action: str
    resource: str
    document_id: UUID | None = None
    document_filename: str | None = None
    chunk_id: UUID | None = None
    confidentiality_tag: str
    authorized: bool
    is_insider: bool
    question: str
    reason: str | None = None
    request_id: str | None = None


class AuditLogPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditLogOut]


# --- ID-HU-FE-004: key ratios dashboard ------------------------------------------

class CatalogDocumentOut(BaseModel):
    id: UUID
    filename: str
    statements: list[str]  # "income_statement" | "balance_sheet"; empty = couldn't tell
    periods: list[str]     # empty = couldn't tell


class CatalogEntityOut(BaseModel):
    entity: str
    key: str
    periods: list[str]  # chronological
    documents: list[CatalogDocumentOut]


class DashboardCatalogOut(BaseModel):
    entities: list[CatalogEntityOut]


class DashboardRefreshRequest(BaseModel):
    entity: str = Field(min_length=1)
    # Defaults to every period found in the entity's documents (most recent ones,
    # up to DASHBOARD_MAX_PERIODS). "2024", "FY2024", "Q3 2026"…
    periods: list[str] | None = None


class DriverOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    previous: float | None = None
    current: float | None = None
    unit: str  # "percent" | "times" | "amount"
    change: float | None = None
    note: str | None = None


class OutlierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    previous_period: str | None = None
    previous_value: float | None = None
    change: float | None = None
    absolute_change: float | None = None
    method: str
    message: str
    drivers: list[DriverOut] = []


class DashboardPointOut(BaseModel):
    period: str
    # calculated | needs_review | not_calculable | not_calculated | restricted.
    # not_calculable: the ratio can't be calculated for this period (an input
    # or a whole statement is missing, see missing_inputs and reasons);
    # not_calculated: the period has documents but hasn't been refreshed yet.
    status: str
    value: float | None = None
    currency: str | None = None
    inputs: list[RatioInputOut] = []
    citations: list[CitationOut] = []
    cross_document: bool = False
    reasons: list[str] = []
    # Statements with no ingested document for this period ("balance_sheet").
    missing_statements: list[str] = []
    # Inputs missing for this period, by name ("Current liabilities").
    missing_inputs: list[str] = []
    stale: bool = False
    outlier: OutlierOut | None = None


class DashboardRatioOut(BaseModel):
    key: str
    name: str
    category: str
    formula: str
    unit: str
    description: str
    points: list[DashboardPointOut]


class DashboardDocumentOut(BaseModel):
    id: str
    filename: str


class DashboardPeriodOut(BaseModel):
    period: str
    calculated: bool
    # No document of the entity covers this period: a gap in its history,
    # shown so it isn't read as a zero or skipped over (ID-HU-BE-011).
    gap: bool = False
    stale: bool = False
    restricted: bool = False
    computed_at: datetime | None = None
    computed_by: str | None = None
    documents: list[DashboardDocumentOut] = []
    notes: list[str] = []


class DashboardOut(BaseModel):
    entity: str
    entity_key: str
    periods: list[DashboardPeriodOut]
    ratios: list[DashboardRatioOut]
    notes: list[str] = []


class RatioTrendOut(BaseModel):
    """One ratio of an entity over its periods, oldest first (ID-HU-BE-011).
    Every period appears, gaps included, each with its sources or the reason
    it couldn't be calculated."""

    entity: str
    entity_key: str
    key: str
    name: str
    category: str
    formula: str
    unit: str
    description: str
    points: list[DashboardPointOut]
    notes: list[str] = []
