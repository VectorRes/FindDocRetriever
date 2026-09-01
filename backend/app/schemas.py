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


class QueryRequest(BaseModel):
    question: str
    top_k: int | None = None


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
