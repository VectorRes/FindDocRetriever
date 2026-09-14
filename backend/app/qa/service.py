from uuid import UUID

from sqlalchemy.orm import Session

from app.qa.conversation import get_or_create_session, get_recent_history, record_turn
from app.qa.graph import get_qa_graph
from app.qa.schemas import GroundedAnswer
from app.retrieval.service import retrieve


def answer_question(
    db: Session,
    question: str,
    top_k: int | None = None,
    session_id: UUID | None = None,
) -> GroundedAnswer:
    """Retrieve relevant sources for `question` and generate a grounded, cited answer.

    Prior turns of `session_id` (or a freshly created session, if none is
    given) are passed along as context so follow-up questions can refer back
    to what was just discussed (ID-HU-FE-001) — the answer is still grounded
    only in newly retrieved SOURCES, never in the history text itself.
    """
    session = get_or_create_session(db, session_id)
    history = get_recent_history(db, session.id)

    chunks = retrieve(db, question=question, top_k=top_k)
    graph = get_qa_graph()
    result = graph.invoke({"question": question, "chunks": chunks, "history": history})
    answer = result["answer"]
    answer.session_id = str(session.id)

    record_turn(db, session.id, question, answer)
    return answer
