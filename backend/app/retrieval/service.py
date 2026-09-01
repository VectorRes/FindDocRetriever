from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings.factory import get_embedding_provider
from app.retrieval.vector_store import similarity_search


@dataclass
class RetrievedChunk:
    document_id: str
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


def retrieve(db: Session, question: str, top_k: int | None = None) -> list[RetrievedChunk]:
    settings = get_settings()
    provider = get_embedding_provider()
    [query_embedding] = provider.embed([question])

    chunks = similarity_search(db, query_embedding, top_k or settings.retrieval_top_k)

    return [
        RetrievedChunk(
            document_id=str(chunk.document_id),
            document_filename=chunk.document.filename,
            sheet_name=chunk.sheet_name,
            cell_range=chunk.cell_range,
            page_number=chunk.page_number,
            text=chunk.text,
            content_type=chunk.content_type,
            reference_number=(
                chunk.pdf_reference.reference_number if chunk.pdf_reference is not None else None
            ),
            extraction_method=chunk.extraction_method,
            confidence=chunk.confidence,
            confidence_label=chunk.confidence_label,
        )
        for chunk in chunks
    ]
