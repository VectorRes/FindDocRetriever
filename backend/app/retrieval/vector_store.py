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
    min_score: float | None = None,
) -> list[tuple[Chunk, float]]:
    """Nearest chunks with their cosine similarity (1 - cosine distance,
    higher is more relevant), best first."""
    # Apply confidentiality, version scope and the relevance floor before
    # LIMIT so excluded chunks never consume retrieval slots and never reach
    # the generation model.
    distance = Chunk.embedding.cosine_distance(query_embedding)
    stmt = select(Chunk, distance).where(_version_scope(document_id))
    if accessible_tags is not None:
        stmt = stmt.where(Chunk.confidentiality_tag.in_(accessible_tags))
    if min_score is not None:
        stmt = stmt.where(distance <= 1 - min_score)
    stmt = stmt.order_by(distance).limit(top_k)
    return [(chunk, 1 - float(dist)) for chunk, dist in db.execute(stmt)]

def has_inaccessible_match(
    db: Session,
    query_embedding: list[float],
    accessible_tags: set[str],
    top_k: int,
    document_id: UUID | None = None,
    min_score: float | None = None,
) -> bool:
    # Check only the nearest relevant matches. Their text is never passed to QA.
    distance = Chunk.embedding.cosine_distance(query_embedding)
    stmt = select(Chunk.confidentiality_tag).where(_version_scope(document_id))
    if min_score is not None:
        stmt = stmt.where(distance <= 1 - min_score)
    tags = db.scalars(stmt.order_by(distance).limit(top_k)).all()
    return any(tag not in accessible_tags for tag in tags)
