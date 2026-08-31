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


class QueryResponse(BaseModel):
    question: str
    results: list[RetrievedChunkOut]
