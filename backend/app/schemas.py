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
