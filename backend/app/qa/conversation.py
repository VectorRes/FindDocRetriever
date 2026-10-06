"""Conversation session persistence for ID-HU-FE-001's follow-up context.

Kept separate from qa/service.py's grounding logic: this module only knows
how to load/create a session and read/write its turns, never how an answer
gets generated.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ConversationMessage, ConversationSession
from app.qa.schemas import GroundedAnswer

# How many prior turns to feed back as context. Kept small: the model only
# needs enough to resolve a follow-up's pronouns/omissions, not a full transcript.
HISTORY_LIMIT = 5


def get_or_create_session(db: Session, session_id: UUID | None) -> ConversationSession:
    if session_id is not None:
        session = db.get(ConversationSession, session_id)
        if session is not None:
            return session
    session = ConversationSession()
    db.add(session)
    db.flush()
    return session


def get_recent_history(db: Session, session_id: UUID, limit: int = HISTORY_LIMIT) -> list[tuple[str, str]]:
    """Return up to `limit` prior (question, answer_text) turns, oldest first."""
    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(limit)
    ).all()
    return [(m.question, m.answer_text) for m in reversed(messages)]


def record_turn(db: Session, session_id: UUID, question: str, answer: GroundedAnswer) -> None:
    if answer.needs_clarification:
        # Recorded so the analyst's reply ("Subsidiary A, 2024") is read as an
        # answer to this clarification on the next turn (ID-HU-BE-009).
        answer_text = f"(asked for clarification) {answer.clarification_question}"
    else:
        answer_text = (
            " ".join(statement.text for statement in answer.statements)
            or "(no confident answer found)"
        )
    citations = [
        {
            "source_id": citation.source_id,
            "document_id": citation.document_id,
            "document_filename": citation.document_filename,
            "sheet_name": citation.sheet_name,
            "cell_range": citation.cell_range,
            "page_number": citation.page_number,
        }
        for statement in answer.statements
        for citation in statement.citations
    ]
    db.add(
        ConversationMessage(
            session_id=session_id,
            question=question,
            answer_text=answer_text,
            citations=citations,
            grounded=answer.grounded,
        )
    )
    db.commit()
