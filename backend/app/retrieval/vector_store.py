from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document


def add_chunks(
    db: Session,
    document_id: UUID,
    chunks: list[tuple],
    embeddings: list[list[float]],
) -> list[Chunk]:
    """Persist chunks. Optional tuple fields: content_type, table_id, reference_id,
    extraction_method, confidence, confidence_label."""
    rows = []
    for item, embedding in zip(chunks, embeddings):
        text, sheet_name, cell_range, page_number = item[:4]
        content_type = item[4] if len(item) > 4 else "text"
        pdf_table_id = item[5] if len(item) > 5 else None
        pdf_reference_id = item[6] if len(item) > 6 else None
        extraction_method = item[7] if len(item) > 7 else "native"
        confidence = item[8] if len(item) > 8 else None
        confidence_label = item[9] if len(item) > 9 else None
        row = Chunk(document_id=document_id, sheet_name=sheet_name, cell_range=cell_range,
                    page_number=page_number, text=text, embedding=embedding,
                    content_type=content_type, pdf_table_id=pdf_table_id,
                    pdf_reference_id=pdf_reference_id, extraction_method=extraction_method,
                    confidence=confidence, confidence_label=confidence_label)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _version_scope(document_id: UUID | None):
    """ID-HU-BE-015: by default only each version group's default version is
    searchable, so superseded (or pending-approval) versions never compete
    with the source of truth. An explicit `document_id` scopes the search to
    that one document instead — including a superseded one, for audit."""
    if document_id is not None:
        return Chunk.document_id == document_id
    return Chunk.document_id.in_(select(Document.id).where(Document.is_current.is_(True)))


def similarity_search(
    db: Session,
    query_embedding: list[float],
    top_k: int,
    accessible_tags: set[str] | None = None,
    document_id: UUID | None = None,
) -> list[Chunk]:
    # Apply confidentiality and version scope before LIMIT so excluded chunks
    # never consume retrieval slots and never reach the generation model.
    stmt = select(Chunk).where(_version_scope(document_id))
    if accessible_tags is not None:
        stmt = stmt.where(Chunk.confidentiality_tag.in_(accessible_tags))
    stmt = stmt.order_by(Chunk.embedding.cosine_distance(query_embedding)).limit(top_k)
    return list(db.scalars(stmt))

def has_inaccessible_match(
    db: Session,
    query_embedding: list[float],
    accessible_tags: set[str],
    top_k: int,
    document_id: UUID | None = None,
) -> bool:
    # Check only the nearest matches. Their text is never passed to QA.
    stmt = (
        select(Chunk.confidentiality_tag)
        .where(_version_scope(document_id))
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    tags = db.scalars(stmt).all()
    return any(tag not in accessible_tags for tag in tags)
