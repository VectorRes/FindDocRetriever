from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk


def add_chunks(
    db: Session,
    document_id: UUID,
    chunks: list[tuple[str, str | None, str | None]],
    embeddings: list[list[float]],
) -> list[Chunk]:
    """chunks: list of (text, sheet_name, cell_range), same order as embeddings."""
    rows = []
    for (text, sheet_name, cell_range), embedding in zip(chunks, embeddings):
        row = Chunk(
            document_id=document_id,
            sheet_name=sheet_name,
            cell_range=cell_range,
            text=text,
            embedding=embedding,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def similarity_search(db: Session, query_embedding: list[float], top_k: int) -> list[Chunk]:
    # No joinedload here: combining it with ORDER BY on a computed distance
    # expression + LIMIT makes SQLAlchemy wrap the query in a subquery that
    # drops the distance expression, silently returning zero rows. Access
    # to chunk.document below lazy-loads instead (fine at this top_k scale).
    stmt = (
        select(Chunk)
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    return list(db.scalars(stmt))
