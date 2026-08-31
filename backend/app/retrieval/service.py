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
        )
        for chunk in chunks
    ]
