from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit import Denial, record_denials, record_granted
from app.config import get_settings
from app.embeddings.factory import get_embedding_provider
from app.retrieval.access import can_access, parse_roles
from app.retrieval.vector_store import has_inaccessible_match, inaccessible_matches, similarity_search


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
    # Cosine similarity to the question (1 - cosine distance); higher is more relevant.
    score: float | None = None
    # ID-HU-BE-016: set by app/qa/source_hierarchy.annotate_sources for answers.
    source_tier: str | None = None
    approved: bool | None = None


def _min_score(min_score: float | None) -> float | None:
    """`min_score` if given, else RETRIEVAL_MIN_SCORE; 0 or less means no floor."""
    value = get_settings().retrieval_min_score if min_score is None else min_score
    return value if value > 0 else None


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
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """Nearest accessible chunks to `question`, ranked by relevance, from each
    document's default version — or only from `document_id` when given
    (ID-HU-BE-015).

    ID-HU-BE-007: only chunks the user's roles may see are considered, and
    only those at least `min_score` similar to the question (default
    RETRIEVAL_MIN_SCORE). When nothing is relevant the list is empty."""
    settings = get_settings()
    provider = get_embedding_provider()
    [query_embedding] = provider.embed([question])
    roles = parse_roles(user_roles)
    allowed_tags = _allowed_tags(db, roles)
    limit = top_k or settings.retrieval_top_k
    matches = similarity_search(
        db, query_embedding, limit,
        accessible_tags=allowed_tags, document_id=document_id,
        min_score=_min_score(min_score),
    )
    chunks = [chunk for chunk, _ in matches]

    # Every returned source is an auditable retrieval. This deliberately logs
    # authorized restricted access too, not only denied attempts.
    for chunk in chunks:
        record_granted(
            db, user_id=user_id, roles=roles, document_id=chunk.document_id, chunk_id=chunk.id,
            document_filename=chunk.document.filename, tag=chunk.confidentiality_tag, question=question,
        )
    if chunks:
        db.flush()

    # ID-HU-BE-014: content the question would have relied on but the user's
    # roles exclude. Only relevant matches count — always with the relevance
    # floor, even when the caller disabled it for its own results (figure
    # extraction), so unrelated restricted content isn't logged on every query.
    denied = inaccessible_matches(
        db, query_embedding, allowed_tags, limit, document_id=document_id,
        min_score=_min_score(min_score) or _min_score(None),
    )
    record_denials(
        user_id=user_id, roles=roles, question=question,
        denials=[
            Denial(document_id=chunk.document_id, document_filename=chunk.document.filename,
                   chunk_id=chunk.id, tag=chunk.confidentiality_tag)
            for chunk in denied
        ],
    )

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
            score=round(score, 4),
        )
        for chunk, score in matches
    ]

def restricted_match_exists(
    db: Session,
    question: str,
    top_k: int | None = None,
    user_roles: str | None = "analyst",
    document_id: UUID | None = None,
    min_score: float | None = None,
) -> bool:
    """Whether content the user can't see is among the matches for
    `question`. Only relevant matches (see `retrieve`) count, so the
    "restricted information" notice isn't shown for unrelated questions."""
    settings = get_settings()
    provider = get_embedding_provider()
    [query_embedding] = provider.embed([question])
    roles = parse_roles(user_roles)
    allowed_tags = _allowed_tags(db, roles)
    return has_inaccessible_match(
        db, query_embedding, allowed_tags, top_k or settings.retrieval_top_k,
        document_id=document_id, min_score=_min_score(min_score),
    )
