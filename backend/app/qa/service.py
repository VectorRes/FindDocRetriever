from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Document
from app.qa.conversation import (
    get_or_create_session,
    get_recent_history,
    record_turn,
    resolve_clarification_reply,
)
from app.qa.formula_context import add_formula_precedents, explain_formulas
from app.qa.graph import get_qa_graph
from app.qa.schemas import GroundedAnswer, GroundedStatement, VersionUsed
from app.qa.source_hierarchy import annotate_sources, source_notices
from app.qa.query_expansion import retrieve_with_translation
from app.qa.statement_rows import statement_rows
from app.retrieval.service import restricted_match_exists
from app.versioning import current_version_of


def answer_question(
    db: Session,
    question: str,
    top_k: int | None = None,
    session_id: UUID | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    document_id: UUID | None = None,
) -> GroundedAnswer:
    """Retrieve relevant sources for `question` and generate a grounded, cited answer.

    Prior turns of `session_id` (or a freshly created session, if none is
    given) are passed along as context so follow-up questions can refer back
    to what was just discussed (ID-HU-FE-001) — the answer is still grounded
    only in newly retrieved SOURCES, never in the history text itself.

    Sources come from each document's default version (ID-HU-BE-015) unless
    `document_id` scopes the question to one specific version, e.g. a
    superseded one being reviewed for audit.

    ID-HU-BE-016: sources are ranked by tier (ERP/EPM > official report >
    internal report), the highest tier wins for the same figure, and the
    answer's `source_notices` flag draft and lower-tier sources.
    """
    session = get_or_create_session(db, session_id)
    history = get_recent_history(db, session.id)
    asked = question
    question = resolve_clarification_reply(question, history)

    # Bilingual and spread across documents (see app/qa/query_expansion.py).
    chunks, translated = retrieve_with_translation(
        db, question=question, top_k=top_k, user_roles=user_roles, user_id=user_id,
        document_id=document_id,
    )
    # The statements' own rows for what's asked, ahead of passages that only
    # mention it (see app/qa/statement_rows.py).
    found = statement_rows(db, [question, translated], chunks, user_roles, user_id)
    listed = {row.chunk_id for row in found.rows}
    chunks = found.rows + [chunk for chunk in chunks if chunk.chunk_id not in listed]
    # Rows a retrieved formula references, so "how is X calculated?" can be answered.
    chunks = add_formula_precedents(db, chunks, question, user_roles=user_roles, user_id=user_id)
    chunks = annotate_sources(db, chunks)
    restricted = restricted_match_exists(
        db, question=question, top_k=top_k, user_roles=user_roles, document_id=document_id
    )
    restriction_notice = (
        "Part of the relevant information is restricted and was excluded from this answer."
        if restricted else None
    )
    graph = get_qa_graph()
    result = graph.invoke({
        "question": question,
        "chunks": chunks,
        "history": history,
        "restriction_notice": restriction_notice,
        "statement_rows": [row for row in found.rows if row.chunk_id in found.full_match_ids],
    })
    answer = result["answer"]
    # "How is X calculated?": spell out the formula from the workbook itself.
    explanations = explain_formulas(db, question, answer.statements, answer.sources)
    if explanations:
        first_notice = next((i for i, s in enumerate(answer.statements) if s.notice), len(answer.statements))
        answer.statements[first_notice:first_notice] = explanations
    answer.session_id = str(session.id)
    answer.versions_used = describe_versions_used(db, answer.statements)
    by_document = {chunk.document_id: chunk for chunk in chunks}
    answer.source_notices = source_notices(answer, by_document, result.get("set_aside") or [])

    answer.question = asked
    record_turn(db, session.id, asked, answer)
    return answer


def describe_versions_used(db: Session, statements: list[GroundedStatement]) -> list[VersionUsed]:
    """The document versions actually cited by the answer, in citation order."""
    document_ids: list[str] = []
    for statement in statements:
        for citation in statement.citations:
            if citation.document_id not in document_ids:
                document_ids.append(citation.document_id)

    versions: list[VersionUsed] = []
    for document_id in document_ids:
        document = db.get(Document, UUID(document_id))
        if document is None:
            continue
        current = None if document.is_current else current_version_of(db, document)
        versions.append(
            VersionUsed(
                document_id=str(document.id),
                filename=document.filename,
                version_number=document.version_number,
                approval_status=document.approval_status,
                is_current=document.is_current,
                current_version_filename=current.filename if current is not None else None,
                source_tier=document.source_tier,
            )
        )
    return versions
