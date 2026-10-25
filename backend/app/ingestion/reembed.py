"""Re-embed every stored chunk with its document's name (app/ingestion/context.py).

For documents ingested before chunks were embedded with their document's
context; new uploads and synced files already are. One embedding call per
batch of chunks (the OpenAI provider splits large batches itself), committed
document by document, so it can be stopped and run again.

    docker compose exec api python -m app.ingestion.reembed
"""
import sys
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document
from app.embeddings.factory import get_embedding_provider
from app.ingestion.context import embedding_text


def reembed(db: Session, provider=None, report: Callable[[str], None] = lambda _: None) -> int:
    """Re-embed the chunks of every document; returns how many chunks."""
    provider = provider or get_embedding_provider()
    total = 0
    for document_id, filename in db.execute(select(Document.id, Document.filename).order_by(Document.uploaded_at)).all():
        chunks = db.scalars(select(Chunk).where(Chunk.document_id == document_id)).all()
        if not chunks:
            continue
        embeddings = provider.embed([embedding_text(filename, chunk.text) for chunk in chunks])
        for chunk, embedding in zip(chunks, embeddings):
            chunk.embedding = embedding
        db.commit()
        total += len(chunks)
        report(f"{filename}: {len(chunks)} chunks")
    return total


def main() -> int:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        total = reembed(db, report=print)
    finally:
        db.close()
    print(f"Re-embedded {total} chunks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
