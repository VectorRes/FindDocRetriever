from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings.factory import get_embedding_provider
from app.retrieval.access import can_access, parse_roles
from app.retrieval.vector_store import has_inaccessible_match, similarity_search


@dataclass
class RetrievedChunk:
    document_id: str
    document_filename: str
    sheet_name: str | None
    cell_range: str | None
    page_number: int | None
    text: str
    chunk_id: str = ""
    content_type: str = "text"
    reference_number: str | None = None
    extraction_method: str = "native"
    confidence: float | None = None
    confidence_label: str | None = None
    confidentiality_tag: str = "restricted"


def _allowed_tags(db: Session, roles: set[str]) -> set[str]:
    from sqlalchemy import select
    from app.db.models import Chunk
    tags = db.scalars(select(Chunk.confidentiality_tag).distinct()).all()
    return {tag for tag in tags if can_access(tag, roles)}

def retrieve(
    db: Session,
    question: str,
    top_k: int | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    document_id: UUID | None = None,
) -> list[RetrievedChunk]:
    """Nearest accessible chunks to `question`, from each document's default
    version — or only from `document_id` when given (ID-HU-BE-015)."""
    settings = get_settings()
    provider = get_embedding_provider()
    [query_embedding] = provider.embed([question])
    roles = parse_roles(user_roles)
    allowed_tags = _allowed_tags(db, roles)
    chunks = similarity_search(
        db, query_embedding, top_k or settings.retrieval_top_k,
        accessible_tags=allowed_tags, document_id=document_id,
    )

    # Every returned source is an auditable retrieval. This deliberately logs
    # authorized restricted access too, not only denied attempts.
    from app.db.models import RetrievalAuditLog
    for chunk in chunks:
        db.add(RetrievalAuditLog(
            user_id=user_id or "anonymous",
            roles=sorted(roles),
            action="retrieve",
            document_id=chunk.document_id,
            chunk_id=chunk.id,
            confidentiality_tag=chunk.confidentiality_tag,
            authorized=True,
            question=question,
        ))
    if chunks:
        db.flush()

    return [
        RetrievedChunk(
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            document_filename=chunk.document.filename,
            sheet_name=chunk.sheet_name,
            cell_range=chunk.cell_range,
            page_number=chunk.page_number,
            text=chunk.text,
            content_type=chunk.content_type,
            reference_number=(
                chunk.pdf_reference.reference_number
                if chunk.pdf_reference is not None else None
            ),
            extraction_method=chunk.extraction_method,
            confidence=chunk.confidence,
            confidence_label=chunk.confidence_label,
            confidentiality_tag=chunk.confidentiality_tag,
        )
        for chunk in chunks
    ]

def restricted_match_exists(
    db: Session,
    question: str,
    top_k: int | None = None,
    user_roles: str | None = "analyst",
    document_id: UUID | None = None,
) -> bool:
    settings = get_settings()
    provider = get_embedding_provider()
    [query_embedding] = provider.embed([question])
    roles = parse_roles(user_roles)
    allowed_tags = _allowed_tags(db, roles)
    return has_inaccessible_match(
        db, query_embedding, allowed_tags, top_k or settings.retrieval_top_k,
        document_id=document_id,
    )
