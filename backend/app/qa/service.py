from sqlalchemy.orm import Session

from app.qa.graph import get_qa_graph
from app.qa.schemas import GroundedAnswer
from app.retrieval.service import retrieve


def answer_question(db: Session, question: str, top_k: int | None = None) -> GroundedAnswer:
    """Retrieve relevant sources for `question` and generate a grounded, cited answer."""
    chunks = retrieve(db, question=question, top_k=top_k)
    graph = get_qa_graph()
    result = graph.invoke({"question": question, "chunks": chunks})
    return result["answer"]
