from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
    confidentiality_tag: str


class QueryRequest(BaseModel):
    question: str
    top_k: int | None = None
    # Omit on the first question of a conversation; pass back the session_id
    # from the previous AnswerResponse to keep follow-up context (ID-HU-FE-001).
    session_id: UUID | None = None


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


class StatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    citations: list[CitationOut]
    conflicting: bool


class AnswerResponse(BaseModel):
    """Response for ID-HU-BE-008: a grounded answer, every statement cited."""

    model_config = ConfigDict(from_attributes=True)

    question: str
    statements: list[StatementOut]
    sources: list[CitationOut]
    has_conflicts: bool
    # False when no statement could be safely grounded (e.g. no relevant sources
    # were retrieved at all) — see ID-HU-BE-009 for the fuller "no confident
    # answer" / clarification flow built on top of this signal.
    grounded: bool
    # Pass this back as session_id on the next QueryRequest to continue the
    # conversation with follow-up context (ID-HU-FE-001).
    session_id: UUID
    # Set by ID-HU-BE-009 (implemented separately); always False/None for now.
    needs_clarification: bool = False
    clarification_question: str | None = None


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
